"""GPU detection for the vps-gpu and mac-mlx install paths.

Two probe functions:

  detect_gpu()             — probes for an NVIDIA GPU via nvidia-smi (vps-gpu path)
  detect_apple_silicon()   — probes for Apple Silicon via sysctl (mac-mlx path)

Both functions follow the same contract:
- Never raise. All subprocess failures surface as has_gpu/has_apple_silicon=False.
- No Python imports beyond stdlib — avoids pulling torch/mlx at install time.
- Cheap enough to call at install time (single subprocess, 2s timeout each).

Spec: docs/plans/v0.5/02-build-plan.md §2.3.2
Backlog: T-125 (S-42 AC-1)
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import NamedTuple

logger = logging.getLogger(__name__)

_NVIDIA_SMI_TIMEOUT = 2.0  # seconds — generous; nvidia-smi typically returns in ms


@dataclass(frozen=True)
class GPUInfo:
    """Result of a GPU probe.

    Attributes:
        has_gpu: True if at least one NVIDIA GPU was detected.
        gpu_name: Model string (e.g. "NVIDIA GeForce RTX 4090"). Empty if no GPU.
        vram_gb: Total VRAM of the first GPU in gigabytes. 0.0 if no GPU.
        device_count: Number of detected GPUs. 0 if none.
        reason: Human-readable explanation of the probe result. Used for
            remediation messages in the installer.
    """
    has_gpu: bool
    gpu_name: str
    vram_gb: float
    device_count: int
    reason: str


def detect_gpu() -> GPUInfo:
    """Probe the host for an NVIDIA GPU via `nvidia-smi`.

    Returns a `GPUInfo` with `has_gpu=True` if at least one GPU is found,
    with the first GPU's name and VRAM populated. Falls back to a
    populated `reason` string when probing fails.
    """
    smi_path = shutil.which("nvidia-smi")
    if not smi_path:
        return GPUInfo(
            has_gpu=False,
            gpu_name="",
            vram_gb=0.0,
            device_count=0,
            reason="nvidia-smi not found on PATH (no NVIDIA driver installed)",
        )

    try:
        result = subprocess.run(
            [
                smi_path,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return GPUInfo(
            has_gpu=False, gpu_name="", vram_gb=0.0, device_count=0,
            reason="nvidia-smi timed out after 2s",
        )
    except OSError as exc:
        return GPUInfo(
            has_gpu=False, gpu_name="", vram_gb=0.0, device_count=0,
            reason=f"nvidia-smi failed to execute: {exc}",
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "unknown error"
        return GPUInfo(
            has_gpu=False, gpu_name="", vram_gb=0.0, device_count=0,
            reason=f"nvidia-smi exited {result.returncode}: {stderr}",
        )

    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return GPUInfo(
            has_gpu=False, gpu_name="", vram_gb=0.0, device_count=0,
            reason="nvidia-smi returned no device entries",
        )

    # Parse the first line: "name, memory_total_mib"
    first = lines[0]
    try:
        name_part, mem_part = first.split(",", 1)
        gpu_name = name_part.strip()
        vram_mib = float(mem_part.strip())
        vram_gb = round(vram_mib / 1024.0, 2)
    except (ValueError, IndexError):
        return GPUInfo(
            has_gpu=False, gpu_name="", vram_gb=0.0, device_count=0,
            reason=f"nvidia-smi output could not be parsed: {first!r}",
        )

    return GPUInfo(
        has_gpu=True,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        device_count=len(lines),
        reason=f"detected {len(lines)} GPU(s); primary: {gpu_name} ({vram_gb} GB VRAM)",
    )


class AppleSiliconInfo(NamedTuple):
    """Result of an Apple Silicon probe.

    Attributes:
        has_apple_silicon: True if running on Apple Silicon (arm64 Darwin).
        chip_name: Chip model string e.g. "Apple M3 Max". Empty if not detected.
        memory_gb: Total unified memory in gigabytes. 0.0 if not detected.
        reason: Human-readable explanation — used in installer banners.
    """
    has_apple_silicon: bool
    chip_name: str
    memory_gb: float
    reason: str


class MlxModelOption(NamedTuple):
    """A recommended MLX model for a given memory tier."""
    model_id: str
    description: str
    ram_gb: float


def detect_apple_silicon() -> AppleSiliconInfo:
    """Probe the host for Apple Silicon via sysctl.

    Returns AppleSiliconInfo with has_apple_silicon=True only when:
      - platform.system() == "Darwin"
      - platform.machine() == "arm64"

    Memory size comes from sysctl hw.memsize (bytes → GB).
    Chip name comes from sysctl machdep.cpu.brand_string.
    Both sysctl calls are optional — failures produce empty/0 values.
    """
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return AppleSiliconInfo(
            has_apple_silicon=False,
            chip_name="",
            memory_gb=0.0,
            reason="not running on Apple Silicon (requires arm64 macOS)",
        )

    chip_name = _sysctl_str("machdep.cpu.brand_string") or "Apple Silicon"
    memory_gb = 0.0
    raw_mem = _sysctl_str("hw.memsize")
    if raw_mem:
        try:
            memory_gb = round(int(raw_mem) / (1024 ** 3), 1)
        except ValueError:
            pass

    return AppleSiliconInfo(
        has_apple_silicon=True,
        chip_name=chip_name,
        memory_gb=memory_gb,
        reason=(
            f"Apple Silicon detected: {chip_name} "
            f"({memory_gb:.0f} GB unified memory)"
        ),
    )


def recommended_mlx_models(memory_gb: float) -> list[MlxModelOption]:
    """Return MLX model options appropriate for the given unified memory size.

    Models are 4-bit quantized from mlx-community on HuggingFace.
    Options are ordered from recommended-for-headroom to highest-quality.

    Tiers:
      <16 GB  → gemma-3-12b only (~7 GB)
      16-31 GB → gemma-3-12b + Qwen2.5-14B (~9 GB)
      32+ GB   → all three, including Qwen2.5-32B (~20 GB)
    """
    options: list[MlxModelOption] = [
        MlxModelOption(
            model_id="mlx-community/gemma-3-12b-it-4bit",
            description="Gemma 3 12B (4-bit) — ~7 GB, fast",
            ram_gb=7.0,
        ),
    ]
    if memory_gb >= 16:
        options.append(MlxModelOption(
            model_id="mlx-community/Qwen2.5-14B-Instruct-4bit",
            description="Qwen2.5 14B (4-bit) — ~9 GB, recommended balance",
            ram_gb=9.0,
        ))
    if memory_gb >= 32:
        options.append(MlxModelOption(
            model_id="mlx-community/Qwen2.5-32B-Instruct-4bit",
            description="Qwen2.5 32B (4-bit) — ~20 GB, highest quality",
            ram_gb=20.0,
        ))
    return options


def _sysctl_str(key: str) -> str:
    """Run `sysctl -n <key>` and return the stripped stdout, or empty string."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


__all__ = [
    "GPUInfo", "detect_gpu",
    "AppleSiliconInfo", "detect_apple_silicon",
    "MlxModelOption", "recommended_mlx_models",
]
