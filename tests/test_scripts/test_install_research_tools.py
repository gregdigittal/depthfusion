"""Smoke tests for scripts/install-research-tools.sh.

We don't try to simulate systemd or real pip installs — those require
a real host. These tests assert the things that break silently:
syntax validity, --help / --dry-run exit codes, and the idempotent
copy logic.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install-research-tools.sh"


def test_script_exists_and_executable():
    assert SCRIPT.exists(), f"Missing: {SCRIPT}"
    # stat().st_mode & 0o111 checks any-exec bit
    assert SCRIPT.stat().st_mode & 0o111, (
        f"Not executable: {SCRIPT}. chmod +x required."
    )


def test_bash_syntax_valid():
    """bash -n catches syntax errors without executing."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Syntax error:\n{result.stderr}"


def test_help_flag_exits_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    # Should print at least the usage block
    assert "Install" in result.stdout or "install" in result.stdout


def test_unknown_arg_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--not-a-real-flag"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "Unknown" in result.stderr or "unknown" in result.stderr


def test_dry_run_no_side_effects(tmp_path, monkeypatch):
    """--dry-run must not create any files in $HOME."""
    # Point HOME at a fresh tmp dir so we can detect any writes
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {
        "HOME": str(fake_home),
        "PATH": "/usr/bin:/bin",  # minimal but real
    }

    subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    # Prereq check will fail because depthfusion isn't importable in
    # this minimal env, OR it will succeed but do nothing real thanks
    # to --dry-run. Either is acceptable; what we care about is that
    # no files were created in $HOME.
    systemd_dir = fake_home / ".config" / "systemd" / "user"
    corpus_dir = fake_home / ".local" / "share" / "depthfusion"
    assert not systemd_dir.exists(), "Dry-run created systemd dir!"
    assert not corpus_dir.exists(), "Dry-run created corpus dir!"


def test_contains_idempotency_check():
    """The script must detect existing units via cmp, not blind-overwrite."""
    content = SCRIPT.read_text()
    # cmp -s is the idempotent-copy primitive
    assert "cmp -s" in content
    # And it must mention 'already installed' for the skip path
    assert "already installed" in content


def test_contains_systemd_availability_fallback():
    """The script must gracefully degrade when systemctl --user isn't available."""
    content = SCRIPT.read_text()
    # Must have SYSTEMD_AVAILABLE flag logic
    assert "SYSTEMD_AVAILABLE" in content
    # Must mention cron as the fallback (the docs tell users what to do)
    assert "cron" in content.lower() or "loginctl" in content.lower()
