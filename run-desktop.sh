#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -x "${LISTEN_VENV_DIR:-$ROOT_DIR/.venv}/bin/python" ] || [ ! -f "$ROOT_DIR/models/whisper-small-int8/config.json" ]; then
  "$ROOT_DIR/scripts/setup.sh"
fi

if [ ! -d "$ROOT_DIR/node_modules/electron" ]; then
  npm install
fi

exec npm start
