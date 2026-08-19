#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SETUP_NEEDED=0
[ -x "${LISTEN_VENV_DIR:-$ROOT_DIR/.venv}/bin/python" ] || SETUP_NEEDED=1
[ -f "$ROOT_DIR/models/whisper-small-int8/config.json" ] || SETUP_NEEDED=1
if ! command -v ollama >/dev/null 2>&1 && [ ! -x "$ROOT_DIR/.local/ollama/bin/ollama" ]; then SETUP_NEEDED=1; fi

if [ "$SETUP_NEEDED" -eq 1 ] || [ "$#" -gt 0 ]; then
  exec "$ROOT_DIR/scripts/setup.sh" --launch "$@"
fi

VENV_DIR="${LISTEN_VENV_DIR:-$ROOT_DIR/.venv}"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m listen_app.ollama_setup --start-only
export LISTEN_DEFAULT_ASR_MODEL="${LISTEN_DEFAULT_ASR_MODEL:-whisper-small-int8}"
exec python -m uvicorn listen_app.main:app --host 127.0.0.1 --port "${LISTEN_PORT:-8765}"
