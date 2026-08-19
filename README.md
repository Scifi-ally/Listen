# Listen

Listen is an **offline, local-first lecture note-taking app** for laptop use. It is designed for Hindi, English, and mixed Hindi-English (Hinglish) classroom speech. The live transcript, extracted key points, optional WAV recording, and session exports stay on the local filesystem.

## One-command setup

On a fresh Linux checkout, run:

```bash
cd Listen
./run.sh
```

The launcher creates `.venv`, installs the Python/audio/ASR dependencies, downloads the local **faster-whisper-small** speech model into `./models/whisper-small-int8`, downloads the official project-local Ollama runtime into `./.local/ollama`, pulls the local **Qwen2.5 3B** note model into `./models/ollama`, runs a readiness check, and starts the app at <http://127.0.0.1:8765>. After the first setup, `./run.sh` reuses the environment and verifies existing model files instead of downloading them again.

The small model is the default because it is the safer starting point for an RTX 3050 6 GB laptop. To download the larger Whisper model instead, run:

```bash
./scripts/setup.sh --size=medium --launch
```

The project-local Ollama server is bound to `127.0.0.1:11434` and is started automatically by setup. If you already run Ollama system-wide, the setup detects and reuses it. Use `--skip-ollama` only if you intentionally want the deterministic heuristic note fallback.

The setup script downloads only during explicit setup and does not execute a remote shell installer or contact any cloud inference API. Once setup is complete, live operation uses the local Whisper model, local Ollama model, and loopback-only inference.

## What is implemented

The app includes a FastAPI backend, a WebSocket stream for incremental transcript and note events, a split-view browser interface, local Markdown/JSON session persistence, optional local WAV recording, start/stop controls, model and microphone selection, VAD sensitivity, note cadence, and a manual transcript correction field for testing or correcting audio output.

| Stage | Local implementation | Runtime behavior |
| --- | --- | --- |
| Audio | `sounddevice` microphone input | Captures directly from the selected local device; no upload path exists |
| VAD | WebRTC VAD when installed, otherwise pause-aware energy VAD | Segments speech around natural pauses and caps long segments |
| ASR | `faster-whisper` adapter | Loads only the downloaded CTranslate2 model directory under `./models` |
| Notes | Ollama adapter with strict JSON prompting | Accepts only localhost Ollama; deterministic heuristic fallback remains available |
| Storage | Timestamped JSON and Markdown, optional PCM WAV | Uses atomic JSON replacement and writes to `./sessions` |
| UI | Static HTML/CSS/JavaScript | Served locally and updated through a local WebSocket |

## Manual setup commands

If you prefer to run the steps separately:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-audio.txt
python -m listen_app.model_setup --size small
python -m listen_app.preflight
python -m uvicorn listen_app.main:app --host 127.0.0.1 --port 8765
```

The model manager verifies `config.json`, tokenizer files, and Whisper model weights after downloading. It never downloads during lecture capture. The application can try a local CPU fallback if CUDA model loading fails when `LISTEN_CPU_FALLBACK=1` (the default).

## Using a session

Enter a title, choose the local Whisper model and microphone, and press **Start listening**. Raw transcript segments appear on the left as VAD detects pauses. Key points are extracted in batches at the selected cadence instead of on every fragment, preserving topic context and reducing GPU contention. Use the correction field to add a sentence manually when testing or correcting ASR output. Press **Stop session** to flush pending notes and close the session; use **Export Markdown** to download the local lecture record.

Choose **Save a local WAV recording** if you want the raw microphone capture retained next to the JSON and Markdown files. Recordings can be large and are ignored by Git.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `LISTEN_SESSIONS_DIR` | `./sessions` | Local JSON, Markdown, and optional WAV output directory |
| `LISTEN_MODELS_DIR` | `./models` | Local faster-whisper model directory |
| `LISTEN_DEFAULT_ASR_MODEL` | `whisper-small-int8` | Model selected by default in the API |
| `LISTEN_ASR_DEVICE` | `cuda` | `cuda` or `cpu` for faster-whisper |
| `LISTEN_CPU_FALLBACK` | `1` | Try local CPU ASR if CUDA model loading fails |
| `LISTEN_VAD_ENGINE` | `auto` | `auto` prefers WebRTC VAD; set `energy` to force the fallback |
| `LISTEN_OLLAMA_HOST` | `http://127.0.0.1:11434` | Loopback-only Ollama endpoint |
| `LISTEN_OLLAMA_MODEL` | `qwen2.5:3b` | Local Ollama model used for notes |

## Validation

Run the regression suite with:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

The tests cover VAD pause segmentation, optional-VAD fallback, incremental note extraction, local Ollama JSON parsing, loopback safety, session path traversal protection, local readiness, API routes, WebSocket snapshot delivery, runner lifecycle behavior, and Markdown/JSON persistence. Real microphone transcription still needs validation with representative Hindi/English/Hinglish classroom recordings on the target laptop; the repository cannot substitute for hardware-specific accuracy and latency checks.

## Offline safety contract

Listen does not contain cloud API keys, telemetry, analytics, remote model downloads, or cloud inference. The only network-shaped runtime code path is the Ollama HTTP API, and it is rejected unless the configured host is loopback. For a completely disconnected runtime, the local heuristic note extractor remains available and ASR becomes visibly unavailable rather than attempting a remote fallback.

> **Accuracy reminder:** Validate the Whisper choice, VAD threshold, bilingual transcription quality, note faithfulness, and end-to-end latency with real classroom audio before relying on the output for academic records.

## References

[1]: https://github.com/SYSTRAN/faster-whisper "SYSTRAN faster-whisper repository"
[2]: https://huggingface.co/Systran/faster-whisper-small "Systran faster-whisper-small model card"
[3]: https://ollama.com/library/qwen2.5 "Official Ollama Qwen2.5 model library"
[4]: https://docs.ollama.com/linux "Official Ollama Linux documentation"

The setup choices are based on the local model formats and commands documented by the official sources above. [1] [2] [3] [4]
