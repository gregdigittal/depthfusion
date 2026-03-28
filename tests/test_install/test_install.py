# tests/test_install/test_install.py
import pytest
import subprocess
import sys


def test_install_help():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--mode" in result.stdout


def test_install_local_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "local", "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout.lower() or "local" in result.stdout.lower()


def test_install_vps_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "vps", "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "vps" in result.stdout.lower() or "dry-run" in result.stdout.lower()


def test_install_rejects_invalid_mode():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "cloud"],
        capture_output=True, text=True
    )
    assert result.returncode != 0


def test_migrate_help():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.migrate", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
