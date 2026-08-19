"""Local FastAPI server for the Listen offline lecture note-taking app."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import (
    DEFAULT_OLLAMA_HOST,
    LectureRunner,
    SessionStore,
    available_local_models,
    local_readiness,
    schedule_broadcast,
)


BASE_DIR = Path(__file__).resolve().parent
store = SessionStore()
app = FastAPI(title="Listen", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class StartRequest(BaseModel):
    title: str = Field(default="Untitled lecture", max_length=160)
    model: str = Field(default="whisper-medium-int8", max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    note_interval_seconds: float = Field(default=10.0, ge=5.0, le=60.0)
    vad_threshold: float = Field(default=0.012, ge=0.001, le=0.2)
    audio_device: str | None = Field(default=None, max_length=200)
    record_audio: bool = False


class ManualTranscriptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Runtime:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sockets: dict[str, set[WebSocket]] = {}
        self.runners: dict[str, LectureRunner] = {}

    def setup(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def publish(self, session_id: str, message: dict[str, Any]) -> None:
        if self.loop and not self.loop.is_closed():
            schedule_broadcast(self.loop, self.sockets.setdefault(session_id, set()), message)


runtime = Runtime()


@app.on_event("startup")
async def startup() -> None:
    runtime.setup(asyncio.get_running_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    for session_id, runner in list(runtime.runners.items()):
        runner.stop()
        runtime.runners.pop(session_id, None)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "offline": True, "active_sessions": len(runtime.runners), "readiness": local_readiness()}


@app.get("/api/config")
async def config() -> dict[str, Any]:
    readiness = local_readiness()
    return {
        "offline": True,
        "models_dir": os.getenv("LISTEN_MODELS_DIR", str(BASE_DIR.parent / "models")),
        "default_asr_model": "whisper-medium-int8",
        "asr_options": ["whisper-medium-int8", "whisper-small-int8"],
        "local_models": readiness["local_models"],
        "default_llm_model": os.getenv("LISTEN_OLLAMA_MODEL", "qwen2.5:3b"),
        "ollama_host": os.getenv("LISTEN_OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        "microphone": "local-only",
        "readiness": readiness,
    }


@app.get("/api/audio/devices")
async def audio_devices() -> dict[str, Any]:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [
            {"index": index, "name": device["name"], "channels": device["max_input_channels"]}
            for index, device in enumerate(devices)
            if device["max_input_channels"] > 0
        ]
        return {"available": True, "devices": inputs}
    except ImportError:
        return {"available": False, "devices": [], "detail": "Install requirements-audio.txt"}
    except Exception as exc:
        return {"available": False, "devices": [], "detail": str(exc)}


@app.get("/api/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return store.list()


@app.post("/api/sessions/start")
async def start_session(request: StartRequest) -> dict[str, Any]:
    if runtime.runners:
        raise HTTPException(status_code=409, detail="A lecture session is already active")
    session = store.create(request.title)
    audio_device: int | str | None = request.audio_device
    if isinstance(audio_device, str) and audio_device.isdigit():
        audio_device = int(audio_device)
    runner = LectureRunner(
        session,
        lambda message: runtime.publish(session["id"], message),
        model_name=request.model,
        note_interval_seconds=request.note_interval_seconds,
        vad_threshold=request.vad_threshold,
        store=store,
        audio_device=audio_device,
        record_audio=request.record_audio,
    )
    runtime.runners[session["id"]] = runner
    try:
        runner.start()
    except Exception as exc:
        runtime.runners.pop(session["id"], None)
        session["ended_at"] = session["started_at"]
        store.save(session)
        raise HTTPException(status_code=500, detail=f"Could not start local pipeline: {exc}") from exc
    runtime.publish(session["id"], {"type": "session_started", "session": session})
    return {"session": session, "ws_path": f"/ws/{session['id']}"}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str) -> dict[str, Any]:
    runner = runtime.runners.pop(session_id, None)
    if not runner:
        raise HTTPException(status_code=404, detail="Active session not found")
    runner.stop()
    return {"session": store.get(session_id)}


@app.post("/api/sessions/{session_id}/transcript")
async def manual_transcript(session_id: str, request: ManualTranscriptRequest) -> dict[str, Any]:
    runner = runtime.runners.get(session_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Active session not found")
    item = runner.add_manual_transcript(request.text)
    if not item:
        raise HTTPException(status_code=400, detail="Transcript text is empty")
    return {"item": item}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    if not store.is_valid_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str) -> FileResponse:
    if not store.is_valid_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    session = store.get(session_id)
    path = store.root / f"{session_id}.md"
    if not session or not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    return FileResponse(path, media_type="text/markdown", filename=f"{session_id}.md")


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    if not store.is_valid_id(session_id):
        await websocket.send_json({"type": "error", "message": "Invalid session id"})
        await websocket.close(code=1008)
        return
    session = store.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close(code=1008)
        return
    sockets = runtime.sockets.setdefault(session_id, set())
    sockets.add(websocket)
    await websocket.send_json({"type": "session_snapshot", "session": session})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        sockets.discard(websocket)
    except Exception:
        sockets.discard(websocket)
