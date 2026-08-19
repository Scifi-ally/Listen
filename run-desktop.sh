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

if [ ! -x "$VENV_PYTHON" ] || [ "$MODEL_READY" -eq 0 ]; then
  "$ROOT_DIR/scripts/setup.sh"
fi

if [ ! -d "$ROOT_DIR/node_modules/electron" ]; then
  npm install
fi

exec npm start
