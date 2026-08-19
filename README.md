# Listen

Listen is an **offline, local-first lecture note-taking app** for laptop use. It is designed for Hindi, English, and mixed Hindi-English (Hinglish) classroom speech. The live transcript, extracted key points, optional WAV recording, and session exports stay on the local filesystem.

## What is implemented

The app includes a FastAPI backend, a WebSocket stream for incremental transcript and note events, a split-view browser interface, local Markdown/JSON session persistence, optional local WAV recording, start/stop controls, model and microphone selection, VAD sensitivity, note cadence, and a manual transcript correction field for testing or correcting audio output.

| Stage | Local implementation | Runtime behavior |
| --- | --- | --- |
| Audio | `sounddevice` microphone input | Captures directly from the selected local device; no upload path exists |
| VAD | WebRTC VAD when installed, otherwise pause-aware energy VAD | Segments speech around natural pauses and caps long segments |
| ASR | `faster-whisper` adapter | Loads only a manually placed model directory under `./models`; no model download is triggered |
| Notes | Ollama adapter with strict JSON prompting | Accepts only localhost Ollama; deterministic heuristic fallback remains available |
| Storage | Timestamped JSON and Markdown, optional PCM WAV | Uses atomic JSON replacement and writes to `./sessions` |
| UI | Static HTML/CSS/JavaScript | Served locally and updated through a local WebSocket |

## Start the app

Create a Python environment, install the server requirements, and run the local server:

```bash
cd Listen
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m listen_app.preflight
python -m uvicorn listen_app.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. The interface and manual transcript path work with only the lightweight server requirements. The readiness banner in the UI tells you exactly which local pieces are still missing.

## Enable microphone transcription

Install the optional local audio/ASR packages:

```bash
pip install -r requirements-audio.txt
```

Then place a compatible **faster-whisper model directory** under `./models`. The UI supports `whisper-medium-int8` for accuracy and `whisper-small-int8` for a lighter RTX 3050 workload. The expected layout is:

```text
models/
├── whisper-medium-int8/
│   ├── config.json
│   ├── model.bin or model*.safetensors
│   └── tokenizer.json / vocabulary.json
└── whisper-small-int8/
```

Model weights are intentionally not committed or downloaded by Listen. If the CUDA model cannot load, the application can try a local CPU fallback when `LISTEN_CPU_FALLBACK=1` (the default). Set `LISTEN_ASR_DEVICE=cpu` explicitly if you want to avoid GPU use.

The microphone selector is populated from local audio devices. Choose **Save a local WAV recording** if you want the raw microphone capture retained next to the JSON and Markdown session files. Recordings can be large; they are ignored by Git along with other generated session files.

## Enable local key-point extraction

Ollama is optional and must already be installed and running on the same machine with a local model available. Configure it with:

```bash
export LISTEN_OLLAMA_HOST=http://127.0.0.1:11434
export LISTEN_OLLAMA_MODEL=qwen2.5:3b
```

Listen rejects non-loopback Ollama hosts. If Ollama is unavailable or returns invalid JSON, the app uses its deterministic sentence-level fallback rather than calling a cloud service. No cloud API key, telemetry endpoint, or remote inference path is included.

## How to use a session

Enter a title, choose the local Whisper model and microphone, and press **Start listening**. Raw transcript segments appear on the left as VAD detects pauses. Key points are extracted in batches at the selected cadence instead of on every fragment, preserving topic context and reducing GPU contention. Use the correction field to add a sentence manually when testing or correcting ASR output. Press **Stop session** to flush pending notes and close the session; use **Export Markdown** to download the local lecture record.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `LISTEN_SESSIONS_DIR` | `./sessions` | Local JSON, Markdown, and optional WAV output directory |
| `LISTEN_MODELS_DIR` | `./models` | Local faster-whisper model directory |
| `LISTEN_ASR_DEVICE` | `cuda` | `cuda` or `cpu` for faster-whisper |
| `LISTEN_CPU_FALLBACK` | `1` | Try local CPU ASR if CUDA model loading fails |
| `LISTEN_VAD_ENGINE` | `auto` | `auto` prefers WebRTC VAD; set `energy` to force the fallback |
| `LISTEN_OLLAMA_HOST` | `http://127.0.0.1:11434` | Loopback-only Ollama endpoint |
| `LISTEN_OLLAMA_MODEL` | `qwen2.5:3b` | Local Ollama model used for notes |

## Validation

Run the dependency-light regression suite with:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

The tests cover VAD pause segmentation, optional-VAD fallback, incremental note extraction, loopback safety, session path traversal protection, local readiness, WAV/session lifecycle behavior, and Markdown/JSON persistence. Real microphone transcription still needs validation with representative Hindi/English/Hinglish classroom recordings on the target laptop; the repository cannot substitute for that hardware-specific accuracy and latency check.

## Offline safety contract

Listen does not contain cloud API keys, telemetry, analytics, remote model downloads, or cloud inference. The only network-shaped code path is the Ollama HTTP API, and it is rejected unless the configured host is loopback. For a completely disconnected runtime, the local heuristic note extractor remains available and ASR becomes visibly unavailable rather than attempting a remote fallback.

> **Accuracy reminder:** Validate the medium/small Whisper choice, VAD threshold, bilingual transcription quality, note faithfulness, and end-to-end latency with real classroom audio before relying on the output for academic records.
