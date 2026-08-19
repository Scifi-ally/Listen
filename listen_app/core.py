"""Core offline lecture capture pipeline.

The module is deliberately dependency-light. Optional integrations (sounddevice,
faster-whisper, and Ollama) are loaded lazily and never fall back to cloud APIs.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


ROOT_DIR = Path(__file__).resolve().parents[1]
SESSIONS_DIR = Path(os.getenv("LISTEN_SESSIONS_DIR", ROOT_DIR / "sessions"))
MODELS_DIR = Path(os.getenv("LISTEN_MODELS_DIR", ROOT_DIR / "models"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value or "lecture"


@dataclass
class AudioSegment:
    samples: list[float]
    started_at: float
    ended_at: float


class EnergyVAD:
    """Pause-aware VAD with a small pre-roll and configurable sensitivity.

    This is a dependency-free fallback suitable for local microphone input. If
    Silero VAD is added later, it can implement the same feed/finalize contract.
    """

    def __init__(
        self,
        sample_rate: int = 16_000,
        frame_ms: int = 30,
        threshold: float = 0.012,
        speech_start_frames: int = 2,
        silence_end_frames: int = 10,
        min_segment_seconds: float = 0.35,
        max_segment_seconds: float = 15.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size = max(1, int(sample_rate * frame_ms / 1000))
        self.threshold = threshold
        self.speech_start_frames = speech_start_frames
        self.silence_end_frames = silence_end_frames
        self.min_segment_samples = int(min_segment_seconds * sample_rate)
        self.max_segment_samples = int(max_segment_seconds * sample_rate)
        self._carry: list[float] = []
        self._pre_roll: deque[list[float]] = deque(maxlen=3)
        self._pending: list[float] = []
        self._active: list[float] = []
        self._speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._sample_cursor = 0
        self._segment_start = 0.0

    def _is_speech(self, frame: list[float]) -> bool:
        if not frame:
            return False
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        return rms >= self.threshold

    def feed(self, samples: Iterable[float]) -> list[AudioSegment]:
        self._carry.extend(float(sample) for sample in samples)
        emitted: list[AudioSegment] = []
        while len(self._carry) >= self.frame_size:
            frame = self._carry[: self.frame_size]
            del self._carry[: self.frame_size]
            emitted.extend(self._feed_frame(frame))
        return emitted

    def _feed_frame(self, frame: list[float]) -> list[AudioSegment]:
        speech = self._is_speech(frame)
        frame_start = self._sample_cursor / self.sample_rate
        self._sample_cursor += len(frame)
        if not self._speaking:
            if speech:
                self._speech_frames += 1
                self._pending.extend(frame)
            else:
                self._pre_roll.append(frame)
                self._speech_frames = 0
                self._pending.clear()
            if self._speech_frames >= self.speech_start_frames:
                self._speaking = True
                self._active = [sample for old in self._pre_roll for sample in old]
                self._active.extend(self._pending)
                self._pending.clear()
                self._segment_start = max(0.0, frame_start - len(self._active) / self.sample_rate)
            return []

        self._active.extend(frame)
        if speech:
            self._silence_frames = 0
        else:
            self._silence_frames += 1
        if (
            self._silence_frames >= self.silence_end_frames
            and len(self._active) >= self.min_segment_samples
        ) or len(self._active) >= self.max_segment_samples:
            return [self._emit(frame_start + len(frame) / self.sample_rate)]
        return []

    def _emit(self, ended_at: float) -> AudioSegment:
        segment = AudioSegment(self._active[:], self._segment_start, ended_at)
        self._active = []
        self._pending.clear()
        self._pre_roll.clear()
        self._speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        return segment

    def finalize(self) -> list[AudioSegment]:
        if self._carry:
            self._active.extend(self._carry)
            self._carry.clear()
        if self._speaking and len(self._active) >= self.min_segment_samples:
            return [self._emit(self._sample_cursor / self.sample_rate)]
        self._active = []
        self._speaking = False
        return []


class Transcriber(Protocol):
    def transcribe(self, segment: AudioSegment) -> str: ...


class UnavailableTranscriber:
    """Safe runtime fallback that never invents speech."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def transcribe(self, segment: AudioSegment) -> str:
        return ""


class FasterWhisperTranscriber:
    def __init__(self, model_path: str | Path, device: str = "cuda", compute_type: str = "int8") -> None:
        self.model_path = str(model_path)
        self.device = device
        self.compute_type = compute_type
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed") from exc
        if not Path(self.model_path).exists():
            raise RuntimeError(f"Whisper model not found locally: {self.model_path}")
        self.model = WhisperModel(self.model_path, device=device, compute_type=compute_type)

    def transcribe(self, segment: AudioSegment) -> str:
        segments, _ = self.model.transcribe(
            segment.samples,
            language=None,
            beam_size=3,
            vad_filter=False,
            condition_on_previous_text=True,
            temperature=0.0,
        )
        return " ".join(item.text.strip() for item in segments if item.text.strip()).strip()


def create_transcriber(model_name: str, device: str = "cuda") -> Transcriber:
    model_path = MODELS_DIR / model_name
    try:
        return FasterWhisperTranscriber(model_path, device=device, compute_type="int8")
    except RuntimeError as exc:
        return UnavailableTranscriber(str(exc))


@dataclass
class KeyPoint:
    text: str
    category: str = "key point"
    source: str = "local"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: str = field(default_factory=utc_now)


class KeyPointExtractor:
    """Local-only extractor with Ollama support and a deterministic fallback."""

    def __init__(self, model: str = "qwen2.5:3b", host: str | None = None) -> None:
        self.model = model
        self.host = host or os.getenv("LISTEN_OLLAMA_HOST", "http://127.0.0.1:11434")
        if not self.host.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ValueError("LISTEN_OLLAMA_HOST must point to localhost for offline safety")

    def extract(self, transcript: str, previous_notes: list[dict[str, Any]]) -> list[KeyPoint]:
        transcript = transcript.strip()
        if not transcript:
            return []
        try:
            return self._ollama_extract(transcript, previous_notes)
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            return self._heuristic_extract(transcript, previous_notes)

    def _ollama_extract(self, transcript: str, previous_notes: list[dict[str, Any]]) -> list[KeyPoint]:
        previous = "\n".join(f"- {note.get('text', '')}" for note in previous_notes[-30:]) or "(none)"
        prompt = (
            "You are a strict lecture note extractor. Use only the new transcript below. "
            "Do not infer, correct, translate, or add facts. Return JSON only as an array "
            "of objects with text and category. Categories must be one of definition, fact, "
            "example, emphasis, key point. Keep Hindi, English, and Hinglish as spoken. "
            "Exclude greetings, filler, and repetition already present in previous notes.\n\n"
            f"Previous notes:\n{previous}\n\nNew transcript:\n{transcript}"
        )
        payload = json.dumps(
            {"model": self.model, "stream": False, "format": "json", "prompt": prompt}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw = result.get("response", "[]")
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict):
            parsed = parsed.get("notes", [])
        if not isinstance(parsed, list):
            raise ValueError("Ollama returned a non-list response")
        return [
            KeyPoint(str(item["text"]).strip(), str(item.get("category", "key point")))
            for item in parsed
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ]

    def _heuristic_extract(self, transcript: str, previous_notes: list[dict[str, Any]]) -> list[KeyPoint]:
        existing = {re.sub(r"\W+", " ", n.get("text", "")).strip().lower() for n in previous_notes}
        sentences = re.split(r"(?<=[.!?।])\s+|\n+", transcript)
        results: list[KeyPoint] = []
        for sentence in sentences:
            clean = re.sub(r"\s+", " ", sentence).strip(" -•\t")
            if len(clean) < 18:
                continue
            normalized = re.sub(r"\W+", " ", clean).strip().lower()
            if normalized in existing or any(normalized in item.text.lower() for item in results):
                continue
            lowered = clean.lower()
            if any(token in lowered for token in ("define", "means", "परिभाषा", "का अर्थ")):
                category = "definition"
            elif any(token in lowered for token in ("example", "उदाहरण", "जैसे", "for instance")):
                category = "example"
            elif any(token in lowered for token in ("important", "ध्यान", "remember", "मुख्य")):
                category = "emphasis"
            else:
                category = "key point"
            results.append(KeyPoint(clean, category))
        return results


class SessionStore:
    def __init__(self, root: Path = SESSIONS_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, title: str = "Untitled lecture") -> dict[str, Any]:
        now = utc_now()
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        data = {
            "id": session_id,
            "title": title.strip() or "Untitled lecture",
            "started_at": now,
            "ended_at": None,
            "transcript": [],
            "notes": [],
            "audio_path": None,
        }
        self.save(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            path = self.root / f"{data['id']}.json"
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            self._write_markdown(data)

    def get(self, session_id: str) -> dict[str, Any] | None:
        path = self.root / f"{session_id}.json"
        if not path.exists():
            return None
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append({k: data.get(k) for k in ("id", "title", "started_at", "ended_at")})
            except json.JSONDecodeError:
                continue
        return items

    def _write_markdown(self, data: dict[str, Any]) -> None:
        lines = [f"# {data['title']}", "", f"- **Session:** `{data['id']}`", f"- **Started:** {data['started_at']}"]
        if data.get("ended_at"):
            lines.append(f"- **Ended:** {data['ended_at']}")
        lines.extend(["", "## Key points", ""])
        for note in data.get("notes", []):
            category = note.get("category", "key point").title()
            lines.append(f"- **{category}:** {note.get('text', '')}")
        lines.extend(["", "## Raw transcript", ""])
        for item in data.get("transcript", []):
            lines.append(f"- {item.get('text', '')}")
        (self.root / f"{data['id']}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class LectureRunner:
    def __init__(
        self,
        session: dict[str, Any],
        publish: Callable[[dict[str, Any]], None],
        model_name: str = "whisper-medium-int8",
        note_interval_seconds: float = 10.0,
        vad_threshold: float = 0.012,
        store: SessionStore | None = None,
    ) -> None:
        self.session = session
        self.publish = publish
        self.store = store or SessionStore()
        self.note_interval_seconds = max(5.0, min(60.0, float(note_interval_seconds)))
        self.vad = EnergyVAD(threshold=vad_threshold)
        self.transcriber = create_transcriber(model_name)
        self.extractor = KeyPointExtractor()
        self._pending_transcript: list[str] = []
        self._last_note_at = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._audio_queue: queue.Queue[list[float] | None] = queue.Queue()

    @property
    def transcriber_status(self) -> str:
        return getattr(self.transcriber, "reason", "ready")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="listen-audio", daemon=True)
        self._thread.start()

    def push_audio(self, samples: Iterable[float]) -> None:
        self._audio_queue.put(list(samples))

    def stop(self) -> None:
        self._stop.set()
        self._audio_queue.put(None)
        if self._thread:
            self._thread.join(timeout=3)
        self._flush_notes(force=True)
        self.session["ended_at"] = utc_now()
        self.store.save(self.session)
        self.publish({"type": "session_stopped", "session": self.session})

    def add_manual_transcript(self, text: str) -> dict[str, Any] | None:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None
        item = {"id": uuid.uuid4().hex[:10], "text": text, "created_at": utc_now(), "source": "manual"}
        self.session["transcript"].append(item)
        self._pending_transcript.append(text)
        self.store.save(self.session)
        self.publish({"type": "transcript", "item": item})
        self._flush_notes(force=False)
        return item

    def _run(self) -> None:
        self.publish({"type": "status", "status": "listening", "transcriber": self.transcriber_status})
        try:
            import sounddevice as sd
        except ImportError:
            self.publish({"type": "status", "status": "manual_input", "detail": "Install optional audio dependencies to enable microphone capture."})
            return

        def callback(indata: Any, frames: int, callback_time: Any, status: Any) -> None:
            if status:
                self.publish({"type": "status", "status": "audio_warning", "detail": str(status)})
            self.push_audio(indata[:, 0].tolist())

        try:
            with sd.InputStream(samplerate=16_000, channels=1, dtype="float32", callback=callback, blocksize=1_600):
                while not self._stop.is_set():
                    try:
                        chunk = self._audio_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        break
                    for segment in self.vad.feed(chunk):
                        self._transcribe_segment(segment)
                for segment in self.vad.finalize():
                    self._transcribe_segment(segment)
        except Exception as exc:
            self.publish({"type": "error", "message": f"Microphone unavailable: {exc}"})

    def _transcribe_segment(self, segment: AudioSegment) -> None:
        try:
            text = self.transcriber.transcribe(segment).strip()
        except Exception as exc:
            self.publish({"type": "error", "message": f"Transcription failed: {exc}"})
            return
        if not text:
            return
        item = {"id": uuid.uuid4().hex[:10], "text": text, "created_at": utc_now(), "source": "microphone"}
        self.session["transcript"].append(item)
        self._pending_transcript.append(text)
        self.store.save(self.session)
        self.publish({"type": "transcript", "item": item})
        self._flush_notes(force=False)

    def _flush_notes(self, force: bool) -> None:
        if not self._pending_transcript:
            return
        if not force and time.monotonic() - self._last_note_at < self.note_interval_seconds:
            return
        joined = " ".join(self._pending_transcript)
        previous = self.session.get("notes", [])
        notes = self.extractor.extract(joined, previous)
        self._pending_transcript.clear()
        self._last_note_at = time.monotonic()
        for note in notes:
            item = asdict(note)
            self.session["notes"].append(item)
            self.publish({"type": "note", "item": item})
        self.store.save(self.session)


async def broadcast_json(websockets: set[Any], message: dict[str, Any]) -> None:
    dead = []
    for websocket in list(websockets):
        try:
            await websocket.send_json(message)
        except Exception:
            dead.append(websocket)
    for websocket in dead:
        websockets.discard(websocket)


def schedule_broadcast(loop: asyncio.AbstractEventLoop, websockets: set[Any], message: dict[str, Any]) -> None:
    asyncio.run_coroutine_threadsafe(broadcast_json(websockets, message), loop)
