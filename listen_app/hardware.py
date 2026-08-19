"""Portable hardware profiling and adaptive local-model selection.

The profiler intentionally uses standard-library probes and conservative defaults.
It never requires a GPU and never treats a missing probe as a reason to fail.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HardwareProfile:
    platform: str
    cpu_threads: int
    memory_gb: float
    gpu_name: str | None
    gpu_vram_gb: float
    tier: str
    asr_device: str
    whisper_model: str
    whisper_beam_size: int
    note_model: str
    note_interval_seconds: int
    vad_engine: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _memory_gb() -> float:
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as memory_file:
                text = memory_file.read()
            match = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.MULTILINE)
            if match:
                return round(int(match.group(1)) / 1024 / 1024, 1)
        if system == "Windows":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong), ("virtual_avail", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.total / 1024 / 1024 / 1024, 1)
        if system == "Darwin":
            output = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=1)
            return round(int(output.strip()) / 1024 / 1024 / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        pass
    return 0.0


def _nvidia_gpu() -> tuple[str | None, float]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None, 0.0
    try:
        output = subprocess.check_output(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip().splitlines()
        if not output:
            return None, 0.0
        name, memory = [part.strip() for part in output[0].split(",", 1)]
        return name, round(float(memory) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, 0.0


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except (ImportError, AttributeError, RuntimeError, OSError):
        return False


def detect_hardware() -> HardwareProfile:
    threads = max(1, os.cpu_count() or 1)
    memory = _memory_gb()
    gpu_name, gpu_vram = _nvidia_gpu()
    cuda = _cuda_available()
    override = os.getenv("LISTEN_PROFILE", "auto").lower()

    if override in {"light", "balanced", "performance"}:
        tier = override
    elif gpu_vram >= 5.5 and memory >= 12:
        tier = "performance"
    elif gpu_vram >= 2.5 and memory >= 8:
        tier = "balanced"
    elif memory >= 8 and threads >= 4:
        tier = "balanced"
    else:
        tier = "light"

    if tier == "performance":
        whisper_model, beam, note_model, cadence = "whisper-medium-int8", 5, "qwen2.5:3b", 10
    elif tier == "balanced":
        whisper_model, beam, note_model, cadence = "whisper-small-int8", 3, "qwen2.5:3b", 12
    else:
        whisper_model, beam, note_model, cadence = "whisper-tiny-int8", 1, "qwen2.5:1.5b", 18

    requested_device = os.getenv("LISTEN_ASR_DEVICE", "auto").lower()
    asr_device = requested_device if requested_device in {"cpu", "cuda"} else ("cuda" if cuda else "cpu")
    return HardwareProfile(
        platform=platform.system().lower(),
        cpu_threads=threads,
        memory_gb=memory,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        tier=tier,
        asr_device=asr_device,
        whisper_model=whisper_model,
        whisper_beam_size=beam,
        note_model=note_model,
        note_interval_seconds=cadence,
        vad_engine="webrtc" if importlib.util.find_spec("webrtcvad") else "energy",
    )
