"""Print local readiness without contacting Ollama or downloading anything."""

from __future__ import annotations

import json

from .core import local_readiness


def main() -> None:
    print(json.dumps(local_readiness(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
