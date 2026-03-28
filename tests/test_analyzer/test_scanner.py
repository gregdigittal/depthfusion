"""Tests for InstanceScanner."""
from __future__ import annotations

from pathlib import Path

import pytest

from depthfusion.analyzer.scanner import InstanceScanner


@pytest.fixture
def fake_claude_dir(tmp_path: Path) -> Path:
    """Create a fake ~/.claude/ directory structure."""
    claude = tmp_path / ".claude"
    claude.mkdir()

    # hooks dir with two scripts
    hooks = claude / "hooks"
    hooks.mkdir()
    (hooks / "session-start.sh").write_text("#!/bin/bash\necho start")
    (hooks / "pre-compact.sh").write_text("#!/bin/bash\necho compact")

    # commands dir
    commands = claude / "commands"
    commands.mkdir()
    (commands / "recall.md").write_text("# /recall")
    (commands / "goal.md").write_text("# /goal")

    # skills dir
    skills = claude / "skills"
    skills.mkdir()
    skill1 = skills / "code-reviewer"
    skill1.mkdir()
    (skill1 / "SKILL.md").write_text("# Skill")

    # sessions dir with .tmp files
    sessions = claude / "sessions"
    sessions.mkdir()
    (sessions / "session-abc.tmp").write_text("{}")
    (sessions / "session-def.tmp").write_text("{}")
    (sessions / "notes.md").write_text("not a session")

    # memory dir
    memory = claude / "memory"
    memory.mkdir()
    (memory / "preferences.md").write_text("# prefs")
    (memory / "MEMORY.md").write_text("# index")

    return claude


def test_scan_returns_all_expected_keys(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    expected_keys = {
        "hooks", "commands", "skills", "sessions",
        "memory_files", "mcp_tool_count", "depthfusion_installed",
    }
    assert set(result.keys()) == expected_keys


def test_hooks_discovered(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    assert len(result["hooks"]) == 2
    hook_names = [Path(p).name for p in result["hooks"]]
    assert "session-start.sh" in hook_names
    assert "pre-compact.sh" in hook_names


def test_commands_discovered(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    assert len(result["commands"]) == 2


def test_sessions_counted_correctly(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    # Only .tmp files should be counted
    assert len(result["sessions"]) == 2
    for p in result["sessions"]:
        assert p.endswith(".tmp")


def test_memory_files_discovered(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    assert len(result["memory_files"]) == 2


def test_skills_discovered(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    assert "code-reviewer" in result["skills"]


def test_depthfusion_not_installed_when_absent(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    assert result["depthfusion_installed"] is False


def test_depthfusion_detected_via_hook(fake_claude_dir: Path):
    # Add a depthfusion hook
    (fake_claude_dir / "hooks" / "depthfusion-session.sh").write_text("#!/bin/bash")
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    result = scanner.scan()
    assert result["depthfusion_installed"] is True


def test_mcp_tool_count_returns_int(fake_claude_dir: Path):
    scanner = InstanceScanner(claude_dir=fake_claude_dir)
    count = scanner.mcp_tool_count()
    assert isinstance(count, int)
    assert count >= 0


def test_scan_handles_missing_subdirectories(tmp_path: Path):
    """Scan on a completely empty claude dir should not crash."""
    empty_claude = tmp_path / ".claude"
    empty_claude.mkdir()
    scanner = InstanceScanner(claude_dir=empty_claude)
    result = scanner.scan()
    assert result["hooks"] == []
    assert result["commands"] == []
    assert result["skills"] == {}
    assert result["sessions"] == []
    assert result["memory_files"] == []
    assert result["depthfusion_installed"] is False
