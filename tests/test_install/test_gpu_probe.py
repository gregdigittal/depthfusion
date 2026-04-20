# tests/test_install/test_gpu_probe.py
"""GPU probe tests — T-125 / S-42 AC-1.

The probe must be:
  - Robust: every failure mode surfaces as `has_gpu=False` with a reason,
    never an exception.
  - Fast: 2s timeout cap on the subprocess.
  - Accurate: returns correct VRAM and device count on well-formed
    nvidia-smi output.

These tests mock `subprocess.run` and `shutil.which` so they work on any
host — GPU or no GPU.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.install.gpu_probe import GPUInfo, detect_gpu


def _mk_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    p = MagicMock()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


class TestDetectGPU:
    def test_no_nvidia_smi_returns_no_gpu(self):
        with patch("depthfusion.install.gpu_probe.shutil.which", return_value=None):
            info = detect_gpu()
        assert info.has_gpu is False
        assert info.device_count == 0
        assert "nvidia-smi not found" in info.reason

    def test_single_gpu_parses_correctly(self):
        smi_output = "NVIDIA GeForce RTX 4090, 24564\n"
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            return_value=_mk_proc(stdout=smi_output),
        ):
            info = detect_gpu()
        assert info.has_gpu is True
        assert info.gpu_name == "NVIDIA GeForce RTX 4090"
        assert info.device_count == 1
        assert info.vram_gb == round(24564 / 1024.0, 2)

    def test_multi_gpu_reports_count_and_first_card(self):
        smi_output = (
            "NVIDIA A100, 81920\n"
            "NVIDIA A100, 81920\n"
            "NVIDIA A100, 81920\n"
        )
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            return_value=_mk_proc(stdout=smi_output),
        ):
            info = detect_gpu()
        assert info.has_gpu is True
        assert info.device_count == 3
        assert info.gpu_name == "NVIDIA A100"

    def test_nvidia_smi_nonzero_exit_returns_no_gpu(self):
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            return_value=_mk_proc(returncode=1, stderr="driver not loaded"),
        ):
            info = detect_gpu()
        assert info.has_gpu is False
        assert "exited 1" in info.reason
        assert "driver not loaded" in info.reason

    def test_nvidia_smi_empty_output_returns_no_gpu(self):
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            return_value=_mk_proc(stdout=""),
        ):
            info = detect_gpu()
        assert info.has_gpu is False
        assert "no device entries" in info.reason

    def test_nvidia_smi_timeout_returns_no_gpu(self):
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            side_effect=subprocess.TimeoutExpired("nvidia-smi", 2.0),
        ):
            info = detect_gpu()
        assert info.has_gpu is False
        assert "timed out" in info.reason

    def test_nvidia_smi_oserror_returns_no_gpu(self):
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            side_effect=OSError("permission denied"),
        ):
            info = detect_gpu()
        assert info.has_gpu is False
        assert "failed to execute" in info.reason

    def test_malformed_nvidia_smi_output_returns_no_gpu(self):
        """If the CSV doesn't match `name,memory` schema, surface gracefully."""
        with patch(
            "depthfusion.install.gpu_probe.shutil.which", return_value="/usr/bin/nvidia-smi",
        ), patch(
            "depthfusion.install.gpu_probe.subprocess.run",
            return_value=_mk_proc(stdout="not-a-csv-line\n"),
        ):
            info = detect_gpu()
        assert info.has_gpu is False
        assert "could not be parsed" in info.reason

    def test_gpu_info_is_frozen_dataclass(self):
        """GPUInfo is frozen so callers cannot mutate the probe result."""
        from dataclasses import FrozenInstanceError
        info = GPUInfo(
            has_gpu=True, gpu_name="x", vram_gb=8.0, device_count=1, reason="ok",
        )
        with pytest.raises(FrozenInstanceError):
            info.has_gpu = False  # type: ignore[misc]
