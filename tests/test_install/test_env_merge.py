"""Tests for S-68 — installer env-file merge (preserves user-authored keys).

Covers:
  - Fresh write (no existing file): byte-identical to direct write (S-42 AC-6 preserved)
  - Existing file with no DepthFusion keys: new lines appended, old lines untouched
  - Existing file with user-authored API key: key preserved verbatim
  - Existing file with outdated mode key: updated to new value
  - Existing file with chmod 600: permissions preserved after merge
  - Comment and blank lines preserved
  - Warning emitted when a key's value changes
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from depthfusion.install.install import (
    _LOCAL_ENV_LINES,
    _parse_env_file,
    _write_env_config,
)

# ---------------------------------------------------------------------------
# _parse_env_file
# ---------------------------------------------------------------------------

class TestParseEnvFile:
    def test_parses_key_value_lines(self, tmp_path):
        p = tmp_path / "test.env"
        p.write_text("FOO=bar\nBAZ=qux\n")
        parsed = _parse_env_file(p)
        assert parsed[0] == ("FOO", "bar", "FOO=bar")
        assert parsed[1] == ("BAZ", "qux", "BAZ=qux")

    def test_comment_lines_have_none_key(self, tmp_path):
        p = tmp_path / "test.env"
        p.write_text("# comment\nFOO=bar\n")
        parsed = _parse_env_file(p)
        assert parsed[0] == (None, None, "# comment")

    def test_blank_lines_have_none_key(self, tmp_path):
        p = tmp_path / "test.env"
        p.write_text("\nFOO=bar\n\n")
        parsed = _parse_env_file(p)
        keys = [k for k, _, _ in parsed]
        assert keys[0] is None

    def test_value_with_equals_sign(self, tmp_path):
        p = tmp_path / "test.env"
        p.write_text("KEY=val=ue\n")
        parsed = _parse_env_file(p)
        assert parsed[0][1] == "val=ue"


# ---------------------------------------------------------------------------
# _write_env_config — fresh install (no existing file)
# ---------------------------------------------------------------------------

class TestWriteEnvConfigFresh:
    def test_fresh_write_matches_direct_join(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        lines = ["DEPTHFUSION_MODE=local", "DEPTHFUSION_GRAPH_ENABLED=true"]
        _write_env_config(lines)
        written = (tmp_path / ".claude" / "depthfusion.env").read_text()
        assert written == "\n".join(lines) + "\n"

    def test_local_mode_byte_identical_to_constant(self, tmp_path, monkeypatch):
        """S-42 AC-6: fresh local install must be byte-identical to _LOCAL_ENV_LINES join."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _write_env_config(_LOCAL_ENV_LINES)
        written = (tmp_path / ".claude" / "depthfusion.env").read_text()
        assert written == "\n".join(_LOCAL_ENV_LINES) + "\n"


# ---------------------------------------------------------------------------
# _write_env_config — merge with existing file
# ---------------------------------------------------------------------------

class TestWriteEnvConfigMerge:
    def _env_file(self, tmp_path: Path) -> Path:
        return tmp_path / ".claude" / "depthfusion.env"

    def _setup_existing(self, tmp_path: Path, content: str) -> Path:
        p = self._env_file(tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def test_user_key_preserved_verbatim(self, tmp_path, monkeypatch):
        """User-authored API key must survive a re-install."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        existing = (
            "DEPTHFUSION_MODE=local\n"
            "DEPTHFUSION_API_KEY=sk-ant-abc123\n"
        )
        self._setup_existing(tmp_path, existing)
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu"])
        result = self._env_file(tmp_path).read_text()
        assert "DEPTHFUSION_API_KEY=sk-ant-abc123" in result

    def test_mode_key_updated_in_place(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(tmp_path, "DEPTHFUSION_MODE=local\n")
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu"])
        result = self._env_file(tmp_path).read_text()
        assert "DEPTHFUSION_MODE=vps-cpu" in result
        assert "DEPTHFUSION_MODE=local" not in result

    def test_new_key_appended_if_absent(self, tmp_path, monkeypatch):
        """Keys in new lines not present in existing file are appended."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(tmp_path, "DEPTHFUSION_MODE=local\n")
        _write_env_config(["DEPTHFUSION_MODE=local", "DEPTHFUSION_GRAPH_ENABLED=true"])
        result = self._env_file(tmp_path).read_text()
        assert "DEPTHFUSION_GRAPH_ENABLED=true" in result

    def test_comments_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(tmp_path, "# my comment\nDEPTHFUSION_MODE=local\n")
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu"])
        result = self._env_file(tmp_path).read_text()
        assert "# my comment" in result

    def test_blank_lines_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(tmp_path, "DEPTHFUSION_MODE=local\n\nDEPTHFUSION_GRAPH_ENABLED=true\n")
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu", "DEPTHFUSION_GRAPH_ENABLED=true"])
        result = self._env_file(tmp_path).read_text()
        assert "\n\n" in result

    def test_chmod_600_preserved(self, tmp_path, monkeypatch):
        """S-68 AC-4: file permissions must survive the merge."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        p = self._setup_existing(tmp_path, "DEPTHFUSION_MODE=local\n")
        os.chmod(p, 0o600)
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu"])
        new_mode = stat.S_IMODE(p.stat().st_mode)
        assert new_mode == 0o600

    def test_update_warning_printed(self, tmp_path, monkeypatch, capsys):
        """S-68 AC-6: changing an existing key prints a warning."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(tmp_path, "DEPTHFUSION_MODE=local\n")
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu"])
        out = capsys.readouterr().out
        assert "Updating DEPTHFUSION_MODE" in out
        assert "local" in out
        assert "vps-cpu" in out

    def test_no_warning_when_value_unchanged(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(tmp_path, "DEPTHFUSION_MODE=local\n")
        _write_env_config(["DEPTHFUSION_MODE=local"])
        out = capsys.readouterr().out
        assert "Updating DEPTHFUSION_MODE" not in out

    def test_haiku_enabled_user_key_preserved(self, tmp_path, monkeypatch):
        """User-set DEPTHFUSION_HAIKU_ENABLED survives a local→vps-cpu re-install."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._setup_existing(
            tmp_path,
            "DEPTHFUSION_MODE=local\nDEPTHFUSION_HAIKU_ENABLED=true\n",
        )
        _write_env_config(["DEPTHFUSION_MODE=vps-cpu", "DEPTHFUSION_GRAPH_ENABLED=true"])
        result = self._env_file(tmp_path).read_text()
        assert "DEPTHFUSION_HAIKU_ENABLED=true" in result
