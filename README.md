# Listen

Listen is an **offline, local-first lecture note-taking app** for laptop use. It is designed for Hindi, English, and mixed Hindi-English (Hinglish) classroom speech. The app keeps the live transcript, extracted key points, and session exports on the local filesystem.

## What is implemented

The app includes a FastAPI backend, a WebSocket stream for incremental transcript and note events, a split-view browser interface, local Markdown/JSON session persistence, start/stop controls, model selection, VAD sensitivity, notes cadence, and a manual transcript correction field for testing or correcting audio output.

The runtime pipeline is intentionally local-only:

| Stage | Local implementation | Offline behavior |
| --- | --- | --- |
| Audio | `sounddevice` microphone input | Captures directly from the laptop; no upload path exists |
| VAD | Pause-aware energy VAD | Segments speech around natural pauses and caps long segments |
| ASR | `faster-whisper` adapter | Loads only a model path under `./models`; no model download is triggered |
| Notes | Ollama adapter with strict JSON prompt | Accepts only localhost Ollama; deterministic heuristic fallback if unavailable |
| Storage | Timestamped JSON and Markdown | Writes to `./sessions` with atomic JSON replacement |
| UI | Static HTML/CSS/JavaScript | Served locally and updated via local WebSocket |

## Quick start

Create a Python environment, install the lightweight server requirements, and start the local server:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn listen_app.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. The app can be used immediately with the manual transcript field. Microphone capture becomes available after installing the optional audio dependencies. The ASR model must be placed manually under `./models`, and Ollama must already have a local model available; neither dependency is downloaded by the application.

```bash
pip install -r requirements-audio.txt
# Example local model layout; choose a compatible faster-whisper model yourself.
mkdir -p models/whisper-medium-int8
# Start Ollama separately with a locally installed model, for example qwen2.5:3b.
```

The `faster-whisper` adapter uses the `whisper-medium-int8` directory by default and falls back to `whisper-small-int8` when selected in the UI. On a 6 GB RTX 3050, use the small model if medium and the local LLM cannot coexist comfortably. Model files are deliberately not committed to Git.

## Validation

Run the dependency-light test suite with:

```bash
python -m unittest discover -s tests -v
```

The tests exercise VAD pause segmentation, duplicate-resistant heuristic note extraction, local session export, and the offline host safety guard. They do not claim microphone or GPU accuracy; those require real Hindi/English classroom recordings and the locally installed models.

## Offline safety contract

Listen does not contain cloud API keys, telemetry, analytics, remote model downloads, or cloud inference. The only network-shaped code path is the Ollama HTTP API, and it is rejected unless the configured host points to loopback. For a completely disconnected runtime, the heuristic note extractor still works and ASR becomes unavailable rather than attempting a remote fallback.

> **Accuracy reminder:** Real bilingual classroom audio should be used to validate the medium/small Whisper choice, VAD threshold, latency, and note faithfulness before relying on the output for academic records.
