# tests/test_install/test_install.py
"""Installer CLI + per-mode install path tests — T-124 / T-127 / T-128 / S-42 / S-56.

Covers:
  * argparse: all three mode tokens (local / vps-cpu / vps-gpu)
  * Rejection of removed --mode=vps alias (S-56)
  * Byte-identity of the local mode env file vs v0.4.x (AC-6)
  * GPU probe refusal path (AC-1)
  * Vps-gpu env file contents when GPU check is bypassed (AC-2)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from depthfusion.install import install as install_mod
from depthfusion.install.gpu_probe import GPUInfo

# ---------------------------------------------------------------------------
# CLI smoke (subprocess) — checks argparse doesn't regress
# ---------------------------------------------------------------------------

def test_install_help_lists_all_modes():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--mode" in result.stdout
    for mode in ("local", "vps-cpu", "vps-gpu"):
        assert mode in result.stdout


def test_install_local_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "local", "--dry-run"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "local" in result.stdout.lower() or "dry-run" in result.stdout.lower()


def test_install_vps_cpu_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "vps-cpu", "--dry-run"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "vps-cpu" in result.stdout.lower() or "dry-run" in result.stdout.lower()


def test_install_vps_gpu_dry_run_with_skip_check():
    """vps-gpu with --skip-gpu-check bypasses nvidia-smi probe."""
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "vps-gpu", "--dry-run", "--skip-gpu-check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "vps-gpu" in result.stdout.lower() or "dry-run" in result.stdout.lower()


def test_install_rejects_invalid_mode():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "cloud"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_migrate_help():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.migrate", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


# ---------------------------------------------------------------------------
# --mode=vps removed in v0.6.0 (S-56 AC-2)
# ---------------------------------------------------------------------------

def test_vps_alias_rejected():
    """--mode=vps must be rejected by argparse with a non-zero exit."""
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "vps"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "vps" in result.stderr  # argparse names the invalid choice
    assert "local" in result.stderr or "vps-cpu" in result.stderr


# ---------------------------------------------------------------------------
# T-128: byte-identity of local-mode env file (AC-6)
# ---------------------------------------------------------------------------

# The exact byte content expected on disk. Changing this is a v0.4.x
# compatibility break — the test fails on purpose to force a release note.
_V04_LOCAL_ENV = (
    "DEPTHFUSION_MODE=local\n"
    "DEPTHFUSION_TIER_AUTOPROMOTE=false\n"
    "DEPTHFUSION_GRAPH_ENABLED=true\n"
)


def test_install_local_env_file_is_byte_identical_to_v04(tmp_path, monkeypatch):
    """Regression: `install_local()` must produce the exact same env file
    bytes as v0.4.x. Changing this breaks external operator scripts that
    hash or diff the file.
    """
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    # Path.home() reads HOME on POSIX
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_mod.install_local(dry_run=False)

    env_file = fake_home / ".claude" / "depthfusion.env"
    assert env_file.exists()
    assert env_file.read_bytes() == _V04_LOCAL_ENV.encode("utf-8")


# ---------------------------------------------------------------------------
# vps-gpu AC-1: refuses on no-GPU host
# ---------------------------------------------------------------------------

def test_install_vps_gpu_refuses_when_no_gpu(capsys, tmp_path, monkeypatch):
    """--mode=vps-gpu with no GPU must exit 2 and print remediation."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    no_gpu = GPUInfo(
        has_gpu=False, gpu_name="", vram_gb=0.0, device_count=0,
        reason="nvidia-smi not found on PATH",
    )
    with patch("depthfusion.install.install.detect_gpu", return_value=no_gpu):
        rc = install_mod.install_vps_gpu(dry_run=False)

    assert rc == 2
    captured = capsys.readouterr()
    assert "requires an NVIDIA GPU" in captured.out
    assert "Remediation" in captured.out
    assert "vps-cpu" in captured.out  # points at the fallback mode
    # Env file must NOT be written on refusal
    assert not (fake_home / ".claude" / "depthfusion.env").exists()


# ---------------------------------------------------------------------------
# vps-gpu AC-2: writes correct env on a GPU host
# ---------------------------------------------------------------------------

def test_install_vps_gpu_writes_correct_env_when_gpu_present(tmp_path, monkeypatch):
    """On a GPU host, --mode=vps-gpu writes env with per-capability backend flags."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    good_gpu = GPUInfo(
        has_gpu=True, gpu_name="RTX 4090", vram_gb=24.0, device_count=1,
        reason="detected 1 GPU(s); primary: RTX 4090 (24.0 GB VRAM)",
    )
    with patch("depthfusion.install.install.detect_gpu", return_value=good_gpu):
        rc = install_mod.install_vps_gpu(dry_run=False, tier_threshold=500)

    assert rc == 0
    env_file = fake_home / ".claude" / "depthfusion.env"
    assert env_file.exists()
    contents = env_file.read_text()
    assert "DEPTHFUSION_MODE=vps-gpu" in contents
    assert "DEPTHFUSION_EMBEDDING_BACKEND=local" in contents
    assert "DEPTHFUSION_GRAPH_ENABLED=true" in contents
    assert "DEPTHFUSION_TIER_THRESHOLD=500" in contents


def test_install_vps_gpu_skip_check_does_not_call_probe(tmp_path, monkeypatch):
    """--skip-gpu-check bypasses nvidia-smi — useful for CI with no GPU."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    with patch("depthfusion.install.install.detect_gpu") as mock_probe:
        rc = install_mod.install_vps_gpu(
            dry_run=False, tier_threshold=500, skip_gpu_check=True,
        )

    assert rc == 0
    mock_probe.assert_not_called()
    assert (fake_home / ".claude" / "depthfusion.env").exists()


# ---------------------------------------------------------------------------
# pyproject extras (AC-5) — structural check, no install
# ---------------------------------------------------------------------------

def test_pyproject_declares_three_mode_extras():
    """pyproject.toml must have [local], [vps-cpu], [vps-gpu] extras."""
    pyproj_text = (Path(__file__).parent.parent.parent / "pyproject.toml").read_text()
    for extra in ("local = ", "vps-cpu = ", "vps-gpu = "):
        assert extra in pyproj_text, (
            f"Missing extras entry '{extra}' in pyproject.toml"
        )


@pytest.mark.parametrize("mode,expected_mode_line", [
    ("local", "DEPTHFUSION_MODE=local"),
    ("vps-cpu", "DEPTHFUSION_MODE=vps-cpu"),
])
def test_each_mode_writes_correct_mode_line(mode, expected_mode_line, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    if mode == "local":
        install_mod.install_local(dry_run=False)
    else:
        install_mod.install_vps_cpu(dry_run=False)

    env_file = fake_home / ".claude" / "depthfusion.env"
    assert expected_mode_line in env_file.read_text()


# ---------------------------------------------------------------------------
# Review-gate regressions: --skip-gpu-check warning + hook-registration path
# ---------------------------------------------------------------------------

def test_skip_gpu_check_warns_when_mode_is_local(capsys):
    """Passing --skip-gpu-check to --mode=local must surface a warning so
    operators notice a stale CI flag rather than silent acceptance.
    """
    rc = install_mod.main(["--mode", "local", "--dry-run", "--skip-gpu-check"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "--skip-gpu-check has no effect" in captured.err
    assert "--mode=vps-gpu" in captured.err


def test_skip_gpu_check_no_warning_when_mode_is_vps_gpu(capsys):
    """The warning must NOT fire for the legitimate vps-gpu use case."""
    rc = install_mod.main([
        "--mode", "vps-gpu", "--dry-run", "--skip-gpu-check",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "has no effect" not in captured.err


# ---------------------------------------------------------------------------
# S-62 / T-195: interactive mode auto-select
# ---------------------------------------------------------------------------

class TestInteractiveModeSelect:
    def test_yes_flag_auto_accepts_recommendation(self, tmp_path, monkeypatch, capsys):
        """`--yes` with no `--mode` probes the host and auto-picks the
        recommended mode without prompting."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        # Force no-GPU recommendation
        from depthfusion.install.gpu_probe import GPUInfo
        with patch("depthfusion.install.install.detect_gpu",
                   return_value=GPUInfo(False, "", 0.0, 0, "no gpu")):
            monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
            rc = install_mod.main(["--yes", "--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "mode selection" in captured.out.lower()
        # No-GPU + no-API-key → recommendation is `local`
        assert "[1] local" in captured.out
        assert "[auto-accept]" in captured.out
        assert "local" in captured.out.lower()

    def test_no_tty_auto_accepts(self, tmp_path, monkeypatch, capsys):
        """When stdin is not a tty (piped, CI), the installer auto-accepts
        the recommendation without prompting — even without `--yes`.
        """
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        from depthfusion.install.gpu_probe import GPUInfo
        with patch("depthfusion.install.install.detect_gpu",
                   return_value=GPUInfo(False, "", 0.0, 0, "no gpu")):
            rc = install_mod.main(["--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "[auto-accept]" in captured.out

    def test_recommendation_for_gpu_host_is_vps_gpu(self, tmp_path, monkeypatch, capsys):
        """With a detected GPU, the recommendation is vps-gpu."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        from depthfusion.install.gpu_probe import GPUInfo
        good_gpu = GPUInfo(True, "RTX 4090", 24.0, 1, "ok")
        with patch("depthfusion.install.install.detect_gpu", return_value=good_gpu):
            with patch("depthfusion.install.install.install_vps_gpu",
                       return_value=0) as mock_install:
                rc = install_mod.main(["--yes"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "NVIDIA GPU detected" in captured.out
        assert "[3] vps-gpu" in captured.out
        mock_install.assert_called_once()

    def test_recommendation_for_cpu_with_api_key_is_vps_cpu(
        self, tmp_path, monkeypatch, capsys,
    ):
        """No GPU + DEPTHFUSION_API_KEY set → vps-cpu recommendation."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-test")
        from depthfusion.install.gpu_probe import GPUInfo
        with patch("depthfusion.install.install.detect_gpu",
                   return_value=GPUInfo(False, "", 0.0, 0, "no gpu")):
            rc = install_mod.main(["--yes", "--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "DEPTHFUSION_API_KEY is set" in captured.out
        assert "vps-cpu" in captured.out.lower()

    def test_explicit_mode_skips_recommendation(self, tmp_path, monkeypatch, capsys):
        """When `--mode` is explicitly provided, no probe/banner runs."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        with patch("depthfusion.install.install.detect_gpu") as mock_probe:
            rc = install_mod.main(["--mode=local", "--dry-run"])
        assert rc == 0
        # Probe NOT called when --mode is explicit
        mock_probe.assert_not_called()
        captured = capsys.readouterr()
        # Banner NOT shown
        assert "mode selection" not in captured.out.lower()


def test_register_hooks_uses_runtime_resolved_home(tmp_path, monkeypatch):
    """Regression: _register_hooks() must resolve ~/.claude/settings.json
    at call time, not at module import. Previously these were module-level
    constants that froze the real home directory path at import — making
    tests silently skip hook registration.
    """
    fake_home = tmp_path / "home"
    claude_dir = fake_home / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    # Minimal settings file with no existing hooks
    settings_file = claude_dir / "settings.json"
    settings_file.write_text('{"hooks": {}}', encoding="utf-8")
    # Create the hook script so it passes the existence check
    (hooks_dir / "depthfusion-pre-compact.sh").write_text("#!/bin/bash\n")
    (hooks_dir / "depthfusion-post-compact.sh").write_text("#!/bin/bash\n")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    install_mod.install_local(dry_run=False)

    # If the constants were still module-level, this would be unchanged.
    data = settings_file.read_text()
    assert "depthfusion-pre-compact.sh" in data
    assert "depthfusion-post-compact.sh" in data


# ---------------------------------------------------------------------------
# Placeholder-key guard (regression for 2026-04-24 incident where
# `sk-ant-api03-your-real-key-here` was live for ~4 weeks, silently
# no-op'ing every Haiku-backed capability via NullBackend fallback.)
# ---------------------------------------------------------------------------

class TestPlaceholderKeyGuard:
    @pytest.mark.parametrize("value", [
        "sk-ant-api03-your-real-key-here",
        "sk-ant-api03-YOUR-real-key-here-appended-garbage",
        "anything-containing-your-real-key-here-substring",
    ])
    def test_is_placeholder_key_flags_documented_markers(self, value):
        assert install_mod._is_placeholder_key(value) is True

    @pytest.mark.parametrize("value", [
        "sk-ant-api03-realbase64characterstring",
        "sk-ant-test",
        "abc-def-here",  # "-here" alone is not a marker — only the full
                         # documented placeholder phrase is matched
    ])
    def test_is_placeholder_key_accepts_real_looking_keys(self, value):
        assert install_mod._is_placeholder_key(value) is False

    def test_is_placeholder_key_treats_none_as_unset(self):
        assert install_mod._is_placeholder_key(None) is False

    def test_is_placeholder_key_treats_empty_as_unset(self):
        assert install_mod._is_placeholder_key("") is False

    def test_check_api_key_warns_on_placeholder(self, monkeypatch, capsys):
        """_check_depthfusion_api_key must emit a loud WARNING when the
        configured key is a documented placeholder — otherwise the
        install appears successful and Haiku silently no-ops."""
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-api03-your-real-key-here")
        install_mod._check_depthfusion_api_key()
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "placeholder" in out.lower()
        assert "NullBackend" in out

    def test_check_api_key_happy_path_unchanged(self, monkeypatch, capsys):
        """Real keys continue to produce the 'found' message."""
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-api03-realvalue")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        install_mod._check_depthfusion_api_key()
        out = capsys.readouterr().out
        assert "DEPTHFUSION_API_KEY found" in out
        assert "WARNING" not in out

    def test_recommend_mode_treats_placeholder_as_no_key(self, monkeypatch):
        """With no GPU and a placeholder key, the recommender must pick
        `local` — not `vps-cpu`. Recommending vps-cpu with a placeholder
        key produces a functional NullBackend install (the exact regression
        that triggered this guard)."""
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-api03-your-real-key-here")
        no_gpu = GPUInfo(False, "", 0.0, 0, "no gpu")
        with patch("depthfusion.install.install.detect_gpu", return_value=no_gpu):
            mode, reason = install_mod._recommend_mode_from_gpu()
        assert mode == "local"
        assert "placeholder" in reason.lower()

    def test_recommend_mode_with_real_key_still_picks_vps_cpu(self, monkeypatch):
        """Real key + no GPU → vps-cpu (the original v0.5.2 behaviour,
        unchanged by the guard)."""
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-api03-realvalue")
        no_gpu = GPUInfo(False, "", 0.0, 0, "no gpu")
        with patch("depthfusion.install.install.detect_gpu", return_value=no_gpu):
            mode, _ = install_mod._recommend_mode_from_gpu()
        assert mode == "vps-cpu"
