# tests/test_install/test_windows_compat.py
"""Windows compatibility tests — T-376 / S-111.

Uses monkeypatch to fake sys.platform == "win32".
No actual Windows machine required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from depthfusion.install import install as install_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_home(tmp_path: Path) -> Path:
    fake_home = tmp_path / "home"
    claude_dir = fake_home / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    return fake_home


# ---------------------------------------------------------------------------
# T-376 test-1: _register_hooks writes .ps1 on Windows
# ---------------------------------------------------------------------------

def test_register_hooks_writes_ps1_on_windows(monkeypatch, tmp_path):
    fake_home = _make_fake_home(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_mod._register_hooks()

    hooks_dir = fake_home / ".claude" / "hooks"
    assert (hooks_dir / "depthfusion-pre-compact.ps1").exists(), "pre-compact .ps1 not written"
    assert (hooks_dir / "depthfusion-post-compact.ps1").exists(), "post-compact .ps1 not written"
    assert not (hooks_dir / "depthfusion-pre-compact.sh").exists(), ".sh written on Windows"
    assert not (hooks_dir / "depthfusion-post-compact.sh").exists(), ".sh written on Windows"

    settings = json.loads((fake_home / ".claude" / "settings.json").read_text())
    hooks = settings.get("hooks", {})
    all_cmds = [
        ih.get("command", "")
        for event_hooks in hooks.values()
        for h in event_hooks
        for ih in h.get("hooks", [])
    ]
    assert any("powershell" in c.lower() for c in all_cmds), "powershell not in hook commands"
    assert not any(c.startswith("bash ") for c in all_cmds), "bash command registered on Windows"


# ---------------------------------------------------------------------------
# T-376 test-2: _register_hooks writes .sh on Linux
# ---------------------------------------------------------------------------

def test_register_hooks_writes_sh_on_linux(monkeypatch, tmp_path):
    fake_home = _make_fake_home(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_mod._register_hooks()

    hooks_dir = fake_home / ".claude" / "hooks"
    assert (hooks_dir / "depthfusion-pre-compact.sh").exists(), "pre-compact .sh not written"
    assert (hooks_dir / "depthfusion-post-compact.sh").exists(), "post-compact .sh not written"
    assert not (hooks_dir / "depthfusion-pre-compact.ps1").exists(), ".ps1 written on Linux"

    settings = json.loads((fake_home / ".claude" / "settings.json").read_text())
    hooks = settings.get("hooks", {})
    all_cmds = [
        ih.get("command", "")
        for event_hooks in hooks.values()
        for h in event_hooks
        for ih in h.get("hooks", [])
    ]
    assert any(c.startswith("bash ") for c in all_cmds), "bash command not registered on Linux"
    assert not any("powershell" in c.lower() for c in all_cmds), "powershell in hook on Linux"


# ---------------------------------------------------------------------------
# T-376 test-3: install_vps_gpu blocked on Windows
# ---------------------------------------------------------------------------

def test_install_vps_gpu_blocked_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(SystemExit) as exc_info:
        install_mod.install_vps_gpu(dry_run=True)
    assert "vLLM" in str(exc_info.value), "expected 'vLLM' in error message"


# ---------------------------------------------------------------------------
# T-376 test-4: install_mac_mlx blocked on Windows
# ---------------------------------------------------------------------------

def test_install_mac_mlx_blocked_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(SystemExit) as exc_info:
        install_mod.install_mac_mlx(dry_run=True)
    assert "Apple Silicon" in str(exc_info.value) or "Windows" in str(exc_info.value), (
        "expected 'Apple Silicon' or 'Windows' in error message"
    )


# ---------------------------------------------------------------------------
# T-376 test-5: dep_checker cross-platform contract (depends on T-368)
# ---------------------------------------------------------------------------

def test_dep_checker_works_on_windows_platform(monkeypatch):
    """Validates dep_checker.py (T-368) cross-platform contract.

    Patches sys.platform to win32 and calls dep_checker.check_deps("vps-cpu").
    Asserts no exception is raised and the result is a list of dicts.
    Skipped automatically if dep_checker is not yet available (T-368 pending).
    """
    try:
        from depthfusion.install import dep_checker  # noqa: PLC0415
    except ImportError:
        pytest.skip("dep_checker (T-368) not yet available — skipping cross-platform test")

    monkeypatch.setattr(sys, "platform", "win32")
    result = dep_checker.check_deps("vps-cpu")
    assert isinstance(result, list), "check_deps must return a list"
    assert all(isinstance(item, dict) for item in result), (
        "each item in check_deps result must be a dict"
    )
