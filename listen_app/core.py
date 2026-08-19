"""Core local-only lecture capture pipeline.

Optional integrations are loaded lazily. The application never downloads models,
contacts a cloud service, or silently falls back to remote inference.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
import wave
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


ROOT_DIR = Path(__file__).resolve().parents[1]
SESSIONS_DIR = Path(os.getenv("LISTEN_SESSIONS_DIR", ROOT_DIR / "sessions"))
MODELS_DIR = Path(os.getenv("LISTEN_MODELS_DIR", ROOT_DIR / "models"))
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_ASR_MODEL = os.getenv("LISTEN_DEFAULT_ASR_MODEL", "whisper-small-int8")
_ALLOWED_CATEGORIES = {"definition", "fact", "example", "emphasis", "key point"}
_SESSION_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_loopback_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _looks_like_whisper_model(path: Path) -> bool:
    model_files = list(path.glob("model.bin")) + list(path.glob("model*.safetensors"))
    return (
        path.is_dir()
        and (path / "config.json").is_file()
        and (path / "tokenizer.json").is_file()
        and (path / "vocabulary.txt").is_file()
        and bool(model_files)
    )


def available_local_models(root: Path = MODELS_DIR) -> list[str]:
    root = Path(root)
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if not path.name.startswith(".") and _looks_like_whisper_model(path))


def local_readiness(root: Path = MODELS_DIR) -> dict[str, Any]:
    return {
        "audio_dependency": importlib.util.find_spec("sounddevice") is not None,
        "vad_dependency": importlib.util.find_spec("webrtcvad") is not None,
        "asr_dependency": importlib.util.find_spec("faster_whisper") is not None,
        "local_models": available_local_models(root),
        "ollama_host": os.getenv("LISTEN_OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        "asr_device": os.getenv("LISTEN_ASR_DEVICE", "cuda"),
    }


@dataclass
class AudioSegment:
    samples: list[float]
    started_at: float
    ended_at: float


class EnergyVAD:
    """Pause-aware, dependency-free VAD with pre-roll and bounded segments."""

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
        self._silence_frames = 0 if speech else self._silence_frames + 1
        if len(self._active) >= self.max_segment_samples:
            return [self._emit(frame_start + len(frame) / self.sample_rate)]
        if self._silence_frames >= self.silence_end_frames:
            if len(self._active) >= self.min_segment_samples:
                return [self._emit(frame_start + len(frame) / self.sample_rate)]
            self._reset()
        return []

    def _reset(self) -> None:
        self._active = []
        self._pending.clear()
        self._pre_roll.clear()
        self._speaking = False
        self._speech_frames = 0
        self._silence_frames = 0

    def _emit(self, ended_at: float) -> AudioSegment:
        segment = AudioSegment(self._active[:], self._segment_start, ended_at)
        self._reset()
        return segment

    def finalize(self) -> list[AudioSegment]:
        if self._carry:
            if self._speaking:
                self._active.extend(self._carry)
            self._carry.clear()
        if self._speaking and len(self._active) >= self.min_segment_samples:
            return [self._emit(self._sample_cursor / self.sample_rate)]
        self._reset()
        return []


class WebRtcVAD(EnergyVAD):
    """WebRTC VAD adapter using the same streaming contract as EnergyVAD."""

    def __init__(self, threshold: float = 0.012, aggressiveness: int = 2) -> None:
        try:
            import webrtcvad
        except ImportError as exc:
            raise RuntimeError("webrtcvad is not installed") from exc
        super().__init__(threshold=threshold, frame_ms=30)
        self._webrtc = webrtcvad.Vad(max(0, min(3, aggressiveness)))

    def _is_speech(self, frame: list[float]) -> bool:
        pcm = bytearray()
        for sample in frame:
            pcm.extend(int(max(-1.0, min(1.0, sample)) * 32767).to_bytes(2, "little", signed=True))
        return self._webrtc.is_speech(bytes(pcm), self.sample_rate)


def create_vad(threshold: float = 0.012) -> EnergyVAD:
    if os.getenv("LISTEN_VAD_ENGINE", "auto").lower() != "energy":
        try:
            return WebRtcVAD(threshold=threshold)
        except RuntimeError:
            pass
    return EnergyVAD(threshold=threshold)


class Transcriber(Protocol):
    def transcribe(self, segment: AudioSegment) -> str: ...


class UnavailableTranscriber:
    """Safe fallback that never invents speech."""

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
            raise RuntimeError("faster-whisper is not installed; install requirements-audio.txt") from exc
        if not Path(self.model_path).is_dir():
            raise RuntimeError(f"Whisper model directory is missing: {self.model_path}")
        try:
            self.model = WhisperModel(self.model_path, device=device, compute_type=compute_type)
        except Exception as exc:
            raise RuntimeError(f"Could not load Whisper on {device}: {exc}") from exc

    def transcribe(self, segment: AudioSegment) -> str:
        try:
            import numpy as np
            audio = np.asarray(segment.samples, dtype=np.float32)
        except ImportError:
            audio = segment.samples
        segments, _ = self.model.transcribe(
            audio,
            language=None,
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=True,
            temperature=0.0,
        )
        return " ".join(item.text.strip() for item in segments if item.text.strip()).strip()


def create_transcriber(model_name: str, device: str | None = None) -> Transcriber:
    model_path = MODELS_DIR / model_name
    preferred = device or os.getenv("LISTEN_ASR_DEVICE", "cuda")
    attempts = [(preferred, "int8")]
    if preferred == "cuda" and os.getenv("LISTEN_CPU_FALLBACK", "1") == "1":
        attempts.append(("cpu", "int8"))
    failures: list[str] = []
    for selected_device, compute_type in attempts:
        try:
            return FasterWhisperTranscriber(model_path, device=selected_device, compute_type=compute_type)
        except RuntimeError as exc:
            failures.append(str(exc))
    return UnavailableTranscriber("; ".join(failures))


@dataclass
class KeyPoint:
    text: str
    category: str = "key point"
    source: str = "local"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: str = field(default_factory=utc_now)


class KeyPointExtractor:
    """Local-only Ollama extractor with a faithful heuristic fallback."""

    def __init__(self, model: str = "qwen2.5:3b", host: str | None = None) -> None:
        self.model = model
        self.host = host or os.getenv("LISTEN_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        if not is_loopback_http_url(self.host):
            raise ValueError("LISTEN_OLLAMA_HOST must be an http loopback URL")

    def extract(
        self,
        transcript: str,
        previous_notes: list[dict[str, Any]],
        recent_transcript: list[str] | None = None,
    ) -> list[KeyPoint]:
        transcript = transcript.strip()
        if not transcript:
            return []
        try:
            return self._ollama_extract(transcript, previous_notes, recent_transcript or [])
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            return self._heuristic_extract(transcript, previous_notes)

    def _ollama_extract(
        self,
        transcript: str,
        previous_notes: list[dict[str, Any]],
        recent_transcript: list[str],
    ) -> list[KeyPoint]:
        previous = "\n".join(f"- {note.get('text', '')}" for note in previous_notes[-30:]) or "(none)"
        context = "\n".join(recent_transcript[-12:]) or transcript
        prompt = (
            "You are a strict lecture note extractor. Use only the new transcript below. "
            "Do not infer, correct, translate, or add facts. Return JSON only as an array "
            "of objects with text and category. Categories must be one of definition, fact, "
            "example, emphasis, key point. Keep Hindi, English, and Hinglish as spoken. "
            "Exclude greetings, filler, and repetition already present in previous notes. "
            "Return at most 8 concise notes.\n\n"
            f"Recent transcript context:\n{context}\n\nPrevious notes:\n{previous}\n\n"
            f"New transcript to extract from:\n{transcript}"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_predict": 512},
                "prompt": prompt,
            }
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
        notes: list[KeyPoint] = []
        for item in parsed[:8]:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            category = str(item.get("category", "key point")).strip().lower()
            if category not in _ALLOWED_CATEGORIES:
                category = "key point"
            notes.append(KeyPoint(str(item["text"]).strip(), category))
        return notes

    def _heuristic_extract(self, transcript: str, previous_notes: list[dict[str, Any]]) -> list[KeyPoint]:
        existing = {
            re.sub(r"\W+", " ", str(note.get("text", ""))).strip().lower()
            for note in previous_notes
        }
        sentences = re.split(r"(?<=[.!?।])\s+|\n+", transcript)
        results: list[KeyPoint] = []
        for sentence in sentences:
            clean = re.sub(r"\s+", " ", sentence).strip(" -•\t")
            if len(clean) < 18:
                continue
            normalized = re.sub(r"\W+", " ", clean).strip().lower()
            if normalized in existing or any(normalized == item.text.lower() for item in results):
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
            if len(results) >= 8:
                break
        return results


class HeuristicOnlyExtractor:
    def __init__(self, reason: str) -> None:
        self.reason = reason
        self._fallback = KeyPointExtractor(host=DEFAULT_OLLAMA_HOST)

    def extract(self, transcript: str, previous_notes: list[dict[str, Any]], recent_transcript: list[str] | None = None) -> list[KeyPoint]:
        return self._fallback._heuristic_extract(transcript, previous_notes)


class WaveRecorder:
    """Non-blocking PCM WAV writer fed from the audio callback."""

    def __init__(self, path: Path, sample_rate: int = 16_000) -> None:
        self.path = path
        self.sample_rate = sample_rate
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=100)
        self._thread = threading.Thread(target=self._run, name="listen-wav-writer", daemon=True)
        self._wave: wave.Wave_write | None = None

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self.path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(self.sample_rate)
        self._thread.start()

    def push(self, samples: Iterable[float]) -> None:
        pcm = bytearray()
        for sample in samples:
            value = max(-1.0, min(1.0, float(sample)))
            pcm.extend(int(value * 32767).to_bytes(2, "little", signed=True))
        try:
            self._queue.put_nowait(bytes(pcm))
        except queue.Full:
            pass

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=3)
        if self._wave:
            self._wave.close()
            self._wave = None

    def _run(self) -> None:
        while True:
            data = self._queue.get()
            if data is None:
                return
            if self._wave:
                self._wave.writeframesraw(data)


class SessionStore:
    def __init__(self, root: Path = SESSIONS_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def is_valid_id(session_id: str) -> bool:
        return bool(_SESSION_ID_RE.fullmatch(session_id))

    def _path_for(self, session_id: str, suffix: str) -> Path | None:
        if not self.is_valid_id(session_id):
            return None
        return self.root / f"{session_id}{suffix}"

    def create(self, title: str = "Untitled lecture") -> dict[str, Any]:
        now = utc_now()
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        data = {
            "id": session_id,
            "title": re.sub(r"\s+", " ", title).strip() or "Untitled lecture",
            "started_at": now,
            "ended_at": None,
            "transcript": [],
            "notes": [],
            "audio_path": None,
        }
        self.save(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        session_id = str(data.get("id", ""))
        path = self._path_for(session_id, ".json")
        if not path:
            raise ValueError("Invalid session id")
        with self._lock:
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            self._write_markdown(data)

    def get(self, session_id: str) -> dict[str, Any] | None:
        path = self._path_for(session_id, ".json")
        if not path or not path.exists():
            return None
        with self._lock:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if self.is_valid_id(str(data.get("id", ""))):
                    items.append({k: data.get(k) for k in ("id", "title", "started_at", "ended_at")})
            except json.JSONDecodeError:
                continue
        return items

    def _write_markdown(self, data: dict[str, Any]) -> None:
        path = self._path_for(str(data["id"]), ".md")
        if not path:
            raise ValueError("Invalid session id")
        lines = [f"# {data['title']}", "", f"- **Session:** `{data['id']}`", f"- **Started:** {data['started_at']}"]
        if data.get("ended_at"):
            lines.append(f"- **Ended:** {data['ended_at']}")
        lines.extend(["", "## Key points", ""])
        for note in data.get("notes", []):
            lines.append(f"- **{note.get('category', 'key point').title()}:** {note.get('text', '')}")
        lines.extend(["", "## Raw transcript", ""])
        for item in data.get("transcript", []):
            lines.append(f"- {item.get('text', '')}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class LectureRunner:
    def __init__(
        self,
        session: dict[str, Any],
        publish: Callable[[dict[str, Any]], None],
        model_name: str = DEFAULT_ASR_MODEL,
        note_interval_seconds: float = 10.0,
        vad_threshold: float = 0.012,
        store: SessionStore | None = None,
        audio_device: int | str | None = None,
        record_audio: bool = False,
    ) -> None:
        self.session = session
        self.publish = publish
        self.store = store or SessionStore()
        self.note_interval_seconds = max(5.0, min(60.0, float(note_interval_seconds)))
        self.vad = create_vad(threshold=vad_threshold)
        self.transcriber = create_transcriber(model_name)
        try:
            self.extractor: Any = KeyPointExtractor(model=os.getenv("LISTEN_OLLAMA_MODEL", "qwen2.5:3b"))
        except ValueError as exc:
            self.extractor = HeuristicOnlyExtractor(str(exc))
        self.audio_device = audio_device
        self.record_audio = record_audio
        self._pending_transcript: list[str] = []
        self._recent_transcript: deque[str] = deque(maxlen=40)
        self._last_note_at = time.monotonic()
        self._stop = threading.Event()
        self._notes_signal = threading.Event()
        self._state_lock = threading.RLock()
        self._audio_thread: threading.Thread | None = None
        self._notes_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue[list[float] | None] = queue.Queue(maxsize=200)
        self._stopped = False
        self._recorder: WaveRecorder | None = None

    @property
    def transcriber_status(self) -> str:
        return getattr(self.transcriber, "reason", "ready")

    @property
    def extractor_status(self) -> str:
        return "heuristic fallback" if isinstance(self.extractor, HeuristicOnlyExtractor) else "ollama local / heuristic fallback"

    def start(self) -> None:
        if self._audio_thread and self._audio_thread.is_alive():
            return
        self._audio_thread = threading.Thread(target=self._run, name="listen-audio", daemon=True)
        self._notes_thread = threading.Thread(target=self._note_loop, name="listen-notes", daemon=True)
        self._notes_thread.start()
        self._audio_thread.start()

    def push_audio(self, samples: Iterable[float]) -> None:
        if self._stop.is_set():
            return
        values = list(samples)
        try:
            self._audio_queue.put_nowait(values)
        except queue.Full:
            self.publish({"type": "status", "status": "audio_overflow", "detail": "Audio queue full; processing is behind."})

    def stop(self) -> None:
        with self._state_lock:
            if self._stopped:
                return
            self._stopped = True
        self._stop.set()
        self._audio_queue.put(None)
        self._notes_signal.set()
        if self._audio_thread:
            self._audio_thread.join(timeout=12)
        if self._notes_thread:
            self._notes_thread.join(timeout=12)
        if self._recorder:
            self._recorder.stop()
            self._recorder = None
        with self._state_lock:
            self.session["ended_at"] = utc_now()
            self.store.save(self.session)
        self.publish({"type": "session_stopped", "session": self.session})

    def add_manual_transcript(self, text: str) -> dict[str, Any] | None:
        text = re.sub(r"\s+", " ", text).strip()
        if not text or self._stopped:
            return None
        self._append_transcript(text, "manual")
        return self.session["transcript"][-1]

    def _run(self) -> None:
        self.publish({
            "type": "status",
            "status": "listening",
            "transcriber": self.transcriber_status,
            "extractor": self.extractor_status,
        })
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            self.publish({"type": "status", "status": "manual_input", "detail": f"Local microphone is unavailable ({exc}); manual transcript input remains available."})
            return

        def callback(indata: Any, frames: int, callback_time: Any, status: Any) -> None:
            if status:
                self.publish({"type": "status", "status": "audio_warning", "detail": str(status)})
            values = indata[:, 0].tolist() if getattr(indata, "ndim", 1) > 1 else indata.tolist()
            if self._recorder:
                self._recorder.push(values)
            self.push_audio(values)

        try:
            if self.record_audio:
                audio_path = SESSIONS_DIR / f"{self.session['id']}.wav"
                self._recorder = WaveRecorder(audio_path)
                self._recorder.start()
                self.session["audio_path"] = str(audio_path)
                self.store.save(self.session)
            stream_kwargs: dict[str, Any] = {
                "samplerate": 16_000,
                "channels": 1,
                "dtype": "float32",
                "callback": callback,
                "blocksize": 1_600,
            }
            if self.audio_device not in (None, ""):
                stream_kwargs["device"] = self.audio_device
            with sd.InputStream(**stream_kwargs):
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
        if text:
            self._append_transcript(text, "microphone")

    def _append_transcript(self, text: str, source: str) -> None:
        item = {"id": uuid.uuid4().hex[:10], "text": text, "created_at": utc_now(), "source": source}
        with self._state_lock:
            self.session["transcript"].append(item)
            self._pending_transcript.append(text)
            self._recent_transcript.append(text)
            self.store.save(self.session)
        self.publish({"type": "transcript", "item": item})
        if time.monotonic() - self._last_note_at >= self.note_interval_seconds:
            self._notes_signal.set()

    def _note_loop(self) -> None:
        while not self._stop.is_set() or self._pending_transcript:
            wait_for = max(0.1, self.note_interval_seconds - (time.monotonic() - self._last_note_at))
            self._notes_signal.wait(timeout=wait_for)
            self._notes_signal.clear()
            if not self._pending_transcript:
                continue
            if not self._stop.is_set() and time.monotonic() - self._last_note_at < self.note_interval_seconds:
                continue
            with self._state_lock:
                transcript = " ".join(self._pending_transcript)
                self._pending_transcript.clear()
                previous = list(self.session.get("notes", []))
                recent = list(self._recent_transcript)
            self.publish({"type": "status", "status": "extracting_notes"})
            notes = self.extractor.extract(transcript, previous, recent)
            with self._state_lock:
                for note in notes:
                    item = asdict(note)
                    self.session["notes"].append(item)
                    self.publish({"type": "note", "item": item})
                self.store.save(self.session)
                self._last_note_at = time.monotonic()


async def broadcast_json(websockets: set[Any], message: dict[str, Any]) -> None:
    """Send one event to every connected local browser and discard dead sockets."""
    dead = []
    for websocket in list(websockets):
        try:
            await websocket.send_json(message)
        except Exception:
            dead.append(websocket)
    for websocket in dead:
        websockets.discard(websocket)


def schedule_broadcast(loop: asyncio.AbstractEventLoop, websockets: set[Any], message: dict[str, Any]) -> None:
    if loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(broadcast_json(websockets, message), loop)
