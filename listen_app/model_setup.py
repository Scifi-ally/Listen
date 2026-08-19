"""Download and verify the local models used by Listen.

This module is intentionally run during setup, not during lecture capture. The
runtime loads only local paths and localhost Ollama after setup completes.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .core import MODELS_DIR, available_local_models

WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}
OLLAMA_MODEL = "qwen2.5:3b"


def whisper_target(size: str) -> Path:
    return MODELS_DIR / f"whisper-{size}-int8"


def whisper_ready(path: Path) -> bool:
    if not (path / "config.json").is_file():
        return False
    model_files = list(path.glob("model.bin")) + list(path.glob("model*.safetensors"))
    return bool(model_files) and (path / "tokenizer.json").is_file() and (path / "vocabulary.txt").is_file()


def download_whisper(size: str, force: bool = False) -> Path:
    target = whisper_target(size)
    if whisper_ready(target) and not force:
        print(f"Whisper {size} already ready at {target}")
        return target
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("huggingface-hub is missing; install requirements-audio.txt first") from exc
    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {WHISPER_REPOS[size]} into {target} …")
    snapshot_download(
        repo_id=WHISPER_REPOS[size],
        local_dir=str(target),
        allow_patterns=[
            "config.json",
            "model.bin",
            "model*.safetensors",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
            "vocabulary.txt",
        ],
    )
    if not whisper_ready(target):
        raise RuntimeError(f"Whisper download completed but verification failed: {target}")
    print(f"Whisper {size} verified")
    return target


def pull_ollama(model: str = OLLAMA_MODEL) -> bool:
    executable = shutil.which("ollama")
    if not executable:
        print("Ollama executable not found; Whisper setup is complete, but local note-model setup is pending.")
        print("Install Ollama from https://ollama.com/download, then run: ollama pull qwen2.5:3b")
        return False
    print(f"Pulling local Ollama model {model} …")
    completed = subprocess.run([executable, "pull", model], check=False)
    if completed.returncode != 0:
        print("Ollama was found but the model pull failed. The app will use its offline heuristic fallback.")
        return False
    print(f"Ollama model {model} is ready")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Listen's local models")
    parser.add_argument("--size", choices=sorted(WHISPER_REPOS), default="small", help="Whisper model size")
    parser.add_argument("--force", action="store_true", help="Re-download the selected Whisper model")
    parser.add_argument("--skip-whisper", action="store_true")
    parser.add_argument("--skip-ollama", action="store_true")
    args = parser.parse_args(argv)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_whisper:
        download_whisper(args.size, force=args.force)
    if not args.skip_ollama:
        pull_ollama()
    print(f"Local model directories: {', '.join(available_local_models()) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
