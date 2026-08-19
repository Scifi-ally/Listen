#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${LISTEN_VENV_DIR:-$ROOT_DIR/.venv}"
WHISPER_SIZE="${LISTEN_WHISPER_SIZE:-auto}"
SKIP_OLLAMA=0
LAUNCH=0
FORCE_MODEL=0

for arg in "$@"; do
  case "$arg" in
    --skip-ollama) SKIP_OLLAMA=1 ;;
    --launch) LAUNCH=1 ;;
    --force-model) FORCE_MODEL=1 ;;
    --size=*) WHISPER_SIZE="${arg#*=}" ;;
    -h|--help)
      cat <<'USAGE'
Usage: ./scripts/setup.sh [options]

Options:
  --launch       Start the local Listen server after setup.
  --skip-ollama  Do not pull qwen2.5:3b, keep the heuristic note fallback.
  --force-model  Re-download the selected Whisper model.
  --size=auto|tiny|small|medium  Select a model or let hardware choose (default: auto).
USAGE
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 is required but was not found." >&2
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Creating local Python environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-audio.txt

if [ "$WHISPER_SIZE" = "auto" ]; then
  WHISPER_SIZE="$(python -c 'from listen_app.hardware import detect_hardware; print(detect_hardware().whisper_model.removeprefix("whisper-").removesuffix("-int8"))')"
fi
MODEL_ARGS=(--size "$WHISPER_SIZE" --skip-ollama)
if [ "$FORCE_MODEL" -eq 1 ]; then MODEL_ARGS+=(--force); fi
python -m listen_app.model_setup "${MODEL_ARGS[@]}"

OLLAMA_MODEL="${LISTEN_OLLAMA_MODEL:-$(python -c 'from listen_app.hardware import detect_hardware; print(detect_hardware().note_model)')}"
if [ "$SKIP_OLLAMA" -eq 0 ]; then
  python -m listen_app.ollama_setup --model "$OLLAMA_MODEL"
else
  echo "Skipping local Ollama setup; Listen will use its heuristic note fallback."
fi

export LISTEN_DEFAULT_ASR_MODEL="auto"
python -m listen_app.preflight

echo
echo "Setup complete. Open http://127.0.0.1:8765"
if [ "$LAUNCH" -eq 1 ]; then
  exec python -m uvicorn listen_app.main:app --host 127.0.0.1 --port "${LISTEN_PORT:-8765}"
fi
