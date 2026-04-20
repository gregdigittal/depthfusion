"""GPU detection for the vps-gpu install path — T-125 / S-42.

Probes the host for an NVIDIA GPU using `nvidia-smi`. We deliberately
avoid importing torch / CUDA libraries because:

  1. Pulling torch in at install time defeats the point of optional
     extras — users on the local-only path shouldn't download a
     multi-GB wheel just to check whether they have a GPU.
  2. `nvidia-smi` is present on any host that has the NVIDIA driver
     installed, which is the minimum bar for running Gemma or
     sentence-transformers on GPU.
  3. Parsing CSV output from nvidia-smi is trivial and has no Python
     dependency beyond stdlib.

Contract
========
- `detect_gpu()` returns a `GPUInfo` dataclass. `has_gpu=False` when
  nvidia-smi is missing, fails to run, or reports zero devices.
- Never raises. All subprocess failures, parsing errors, and missing
  commands surface as `has_gpu=False` with a populated `reason` field.
- Cheap enough to call at install time (single subprocess, 2s timeout).

Spec: docs/plans/v0.5/02-build-plan.md §2.3.2
Backlog: T-125 (S-42 AC-1)
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

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


__all__ = ["GPUInfo", "detect_gpu"]
