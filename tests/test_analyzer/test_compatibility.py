"""Tests for CompatibilityChecker."""
from __future__ import annotations

from pathlib import Path

from depthfusion.analyzer.compatibility import GREEN, RED, YELLOW, CompatibilityChecker
from depthfusion.analyzer.scanner import InstanceScanner


def _make_scanner(tmp_path: Path, **overrides) -> InstanceScanner:
    """Create a scanner pointing at a fake claude dir."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    scanner = InstanceScanner(claude_dir=claude_dir)
    return scanner


def test_check_all_returns_11_keys(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    results = checker.check_all()
    assert set(results.keys()) == {f"C{i}" for i in range(1, 12)}


def test_each_result_has_required_fields(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    results = checker.check_all()
    for key, val in results.items():
        assert "status" in val, f"{key} missing 'status'"
        assert "message" in val, f"{key} missing 'message'"
        assert "detail" in val, f"{key} missing 'detail'"
        assert val["status"] in (GREEN, YELLOW, RED), f"{key} has invalid status: {val['status']}"


def test_c1_green_when_no_tmp_access(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c1_session_format()
    # The actual source files don't open .tmp files, so should be GREEN
    assert result["status"] == GREEN


def test_c2_green_for_low_count(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    # Mock mcp_tool_count to return low value
    scanner.mcp_tool_count = lambda: 5
    checker = CompatibilityChecker(scanner=scanner)
    checker._scan_cache = scanner.scan()
    checker._scan_cache["mcp_tool_count"] = 5
    result = checker.check_c2_mcp_tool_count()
    assert result["status"] == GREEN


def test_c2_yellow_for_75_to_79(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    checker._scan_cache = {"mcp_tool_count": 77, "hooks": [], "commands": [], "skills": {}, "sessions": [], "memory_files": [], "depthfusion_installed": False}
    result = checker.check_c2_mcp_tool_count()
    assert result["status"] == YELLOW


def test_c2_red_for_80_or_more(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    checker._scan_cache = {"mcp_tool_count": 80, "hooks": [], "commands": [], "skills": {}, "sessions": [], "memory_files": [], "depthfusion_installed": False}
    result = checker.check_c2_mcp_tool_count()
    assert result["status"] == RED


def test_c3_yellow_when_no_skills_dir(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c3_skill_registry()
    # No skills dir created, so YELLOW
    assert result["status"] == YELLOW


def test_c3_green_when_skills_dir_with_registry(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    skills_dir = scanner.claude_dir / "skills"
    skills_dir.mkdir()
    (skills_dir / "REGISTRY.md").write_text("# Registry")
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c3_skill_registry()
    assert result["status"] == GREEN


def test_c4_green_when_no_clara(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c4_clara_state()
    assert result["status"] == GREEN


def test_c4_green_when_clara_only_in_node_modules(tmp_path: Path):
    """C4 must not false-positive on npm packages containing 'clara' in node_modules."""
    scanner = _make_scanner(tmp_path)
    # Simulate a node_modules package whose filename contains "clara"
    node_mods = scanner.claude_dir / "node_modules" / "postcss-selector-parser" / "dist" / "selectors"
    node_mods.mkdir(parents=True)
    (node_mods / "clara-helper.js").write_text("// not a CLaRa integration file")
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c4_clara_state()
    assert result["status"] == GREEN, (
        f"Expected GREEN but got {result['status']}: {result['detail']}"
    )


def test_c4_yellow_when_clara_outside_excluded_dirs(tmp_path: Path):
    """C4 must detect CLaRa indicators that are NOT inside excluded directories."""
    scanner = _make_scanner(tmp_path)
    # Create a genuine CLaRa file at the top level of ~/.claude
    (scanner.claude_dir / "clara-state.json").write_text("{}")
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c4_clara_state()
    assert result["status"] == YELLOW


def test_c5_green_when_no_depthfusion_hooks(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c5_stop_hook_ordering()
    assert result["status"] == GREEN


def test_c6_green_when_venv_exists(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    # The project's actual .venv exists, so C6 should be GREEN
    result = checker.check_c6_python_environment()
    assert result["status"] == GREEN  # real venv exists at project root


def test_c7_yellow_when_no_recall(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c7_recall_modification()
    # No commands dir, so YELLOW
    assert result["status"] == YELLOW


def test_c7_green_when_recall_exists(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    commands = scanner.claude_dir / "commands"
    commands.mkdir()
    (commands / "recall.md").write_text("# recall")
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c7_recall_modification()
    assert result["status"] == GREEN


def test_c8_always_green(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c8_supabase_migration()
    assert result["status"] == GREEN


def test_c9_green_when_sandbox_exists(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c9_rlm_sandboxing()
    # sandbox.py was created in this task
    assert result["status"] == GREEN


def test_c10_green_when_no_env_passthrough(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c10_rlm_hook_interference()
    assert result["status"] == GREEN


def test_c11_green_with_default_config(tmp_path: Path):
    scanner = _make_scanner(tmp_path)
    checker = CompatibilityChecker(scanner=scanner)
    result = checker.check_c11_rlm_cost_ceiling()
    assert result["status"] == GREEN
    assert "0.50" in result["message"]
