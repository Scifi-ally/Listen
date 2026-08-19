#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
VENV_DIR="${LISTEN_VENV_DIR:-$ROOT_DIR/.venv}"
VENV_PYTHON="$VENV_DIR/bin/python"

MODEL_READY=0
if [ -x "$VENV_PYTHON" ] && PYTHONPATH="$ROOT_DIR" "$VENV_PYTHON" -c 'from listen_app.core import available_local_models; raise SystemExit(0 if available_local_models() else 1)' 2>/dev/null; then
  MODEL_READY=1
fi

SETUP_NEEDED=0
[ -x "$VENV_PYTHON" ] || SETUP_NEEDED=1
[ "$MODEL_READY" -eq 1 ] || SETUP_NEEDED=1
if ! command -v ollama >/dev/null 2>&1 && [ ! -x "$ROOT_DIR/.local/ollama/bin/ollama" ]; then SETUP_NEEDED=1; fi

if [ "$SETUP_NEEDED" -eq 1 ] || [ "$#" -gt 0 ]; then
  exec "$ROOT_DIR/scripts/setup.sh" --launch "$@"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m listen_app.ollama_setup --start-only
export LISTEN_DEFAULT_ASR_MODEL="${LISTEN_DEFAULT_ASR_MODEL:-auto}"
exec python -m uvicorn listen_app.main:app --host 127.0.0.1 --port "${LISTEN_PORT:-8765}"
