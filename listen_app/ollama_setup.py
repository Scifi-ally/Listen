"""Project-local Ollama bootstrap for Linux.

The setup path downloads only the fixed official Ollama archive URL. Runtime
inference remains on 127.0.0.1 and uses a model directory ignored by Git.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

from .core import ROOT_DIR

OLLAMA_ARCHIVE_URL = "https://ollama.com/download/ollama-linux-amd64.tar.zst"
OLLAMA_ROOT = ROOT_DIR / ".local" / "ollama"
OLLAMA_BINARY = OLLAMA_ROOT / "bin" / "ollama"
OLLAMA_MODELS = ROOT_DIR / "models" / "ollama"
OLLAMA_HOST = "127.0.0.1:11434"


def _safe_extract_zstd_tar(archive: Path, destination: Path) -> None:
    try:
        import zstandard
    except ImportError as exc:
        raise RuntimeError("zstandard is required to extract the local Ollama archive") from exc
    destination.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    target = (destination / member.name).resolve()
                    if not str(target).startswith(str(destination.resolve()) + os.sep):
                        raise RuntimeError(f"Unsafe Ollama archive path: {member.name}")
                    tar.extract(member, destination)


def ensure_binary() -> Path:
    system_binary = shutil.which("ollama")
    if system_binary:
        return Path(system_binary)
    if OLLAMA_BINARY.exists():
        return OLLAMA_BINARY
    cache = ROOT_DIR / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "ollama-linux-amd64.tar.zst"
    print("Downloading the official local Ollama runtime …")
    with urllib.request.urlopen(OLLAMA_ARCHIVE_URL, timeout=30) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    print("Extracting Ollama into .local/ollama …")
    _safe_extract_zstd_tar(archive, OLLAMA_ROOT)
    if not OLLAMA_BINARY.exists():
        raise RuntimeError("Ollama archive did not contain bin/ollama")
    OLLAMA_BINARY.chmod(0o755)
    return OLLAMA_BINARY


def _ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1):
            return True
    except OSError:
        return False


def ensure_server(binary: Path) -> subprocess.Popen[bytes] | None:
    if _ready():
        return None
    OLLAMA_MODELS.mkdir(parents=True, exist_ok=True)
    log_path = ROOT_DIR / ".cache" / "ollama.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    environment = os.environ.copy()
    environment.update({"OLLAMA_HOST": OLLAMA_HOST, "OLLAMA_MODELS": str(OLLAMA_MODELS)})
    process = subprocess.Popen(
        [str(binary), "serve"],
        cwd=ROOT_DIR,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(60):
        if _ready():
            return process
        if process.poll() is not None:
            raise RuntimeError(f"Ollama server exited early; inspect {log_path}")
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Ollama server did not become ready on 127.0.0.1:11434")


def pull(model: str) -> None:
    binary = ensure_binary()
    ensure_server(binary)
    environment = os.environ.copy()
    environment.update({"OLLAMA_HOST": OLLAMA_HOST, "OLLAMA_MODELS": str(OLLAMA_MODELS)})
    result = subprocess.run([str(binary), "pull", model], env=environment, check=False)
    if result.returncode:
        raise RuntimeError(f"Ollama model pull failed with exit code {result.returncode}")
    print(f"Local Ollama model ready: {model}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the local Ollama runtime and note model")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--skip", action="store_true")
    parser.add_argument("--start-only", action="store_true", help="Start local Ollama without pulling a model")
    args = parser.parse_args(argv)
    if not args.skip:
        binary = ensure_binary()
        if args.start_only:
            ensure_server(binary)
            print("Local Ollama server is ready")
        else:
            pull(args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
