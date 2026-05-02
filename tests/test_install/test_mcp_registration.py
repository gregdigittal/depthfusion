"""Tests for S-67 — installer auto-registers MCP server with Claude CLI.

Covers:
  - CLI present + not registered: subprocess invoked
  - CLI present + already registered: subprocess NOT invoked (idempotent)
  - CLI absent: prints manual command, no subprocess
  - Invocation failure: warning printed, install continues (non-fatal)
  - dry_run=True: prints intention without invoking subprocess
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.install.install import _register_mcp_server


class TestRegisterMcpServer:
    def _mock_settings(self, tmp_path: Path, has_depthfusion: bool = False) -> Path:
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        mcp = {"depthfusion": {}} if has_depthfusion else {}
        settings.write_text(json.dumps({"mcpServers": mcp}))
        return settings

    def test_cli_present_not_registered_invokes_subprocess(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._mock_settings(tmp_path, has_depthfusion=False)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            _register_mcp_server()

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "claude" in call_args[0]
        assert "mcp" in call_args
        assert "add" in call_args
        assert "depthfusion" in call_args

    def test_cli_present_already_registered_skips(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._mock_settings(tmp_path, has_depthfusion=True)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            _register_mcp_server()

        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "already registered" in out

    def test_cli_absent_prints_manual_command(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch("shutil.which", return_value=None), \
             patch("subprocess.run") as mock_run:
            _register_mcp_server()

        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "claude mcp add depthfusion" in out
        assert "manually" in out.lower() or "not found" in out.lower()

    def test_invocation_failure_is_non_fatal(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._mock_settings(tmp_path, has_depthfusion=False)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
            # Must not raise
            _register_mcp_server()

        out = capsys.readouterr().out
        assert "Warning" in out or "manually" in out

    def test_dry_run_does_not_invoke_subprocess(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._mock_settings(tmp_path, has_depthfusion=False)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            _register_mcp_server(dry_run=True)

        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "DRY-RUN" in out or "dry" in out.lower()

    def test_no_settings_file_does_not_crash(self, tmp_path, monkeypatch):
        """If settings.json is absent, register proceeds (idempotency probe fails gracefully)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            _register_mcp_server()

        mock_run.assert_called_once()

    def test_subprocess_timeout_is_non_fatal(self, tmp_path, monkeypatch, capsys):
        import subprocess as _subprocess
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._mock_settings(tmp_path, has_depthfusion=False)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run", side_effect=_subprocess.TimeoutExpired(["claude"], 30)):
            _register_mcp_server()

        out = capsys.readouterr().out
        assert "manually" in out.lower() or "Warning" in out
