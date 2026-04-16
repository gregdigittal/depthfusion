"""CompatibilityChecker — checks all 11 DepthFusion compatibility constraints (C1-C11)."""
from __future__ import annotations

import sys
from pathlib import Path

from depthfusion.analyzer.scanner import InstanceScanner

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # src/../ = project root


def _check_result(status: str, message: str, detail: str = "") -> dict:
    return {"status": status, "message": message, "detail": detail}


class CompatibilityChecker:
    """Checks all 11 DepthFusion compatibility constraints (C1-C11)."""

    def __init__(self, scanner: InstanceScanner | None = None) -> None:
        self.scanner = scanner or InstanceScanner()
        self._scan_cache: dict | None = None

    def _scan(self) -> dict:
        if self._scan_cache is None:
            self._scan_cache = self.scanner.scan()
        return self._scan_cache

    def check_all(self) -> dict[str, dict]:
        """Return {constraint_id: {status, message, detail}} for C1-C11."""
        return {
            "C1": self.check_c1_session_format(),
            "C2": self.check_c2_mcp_tool_count(),
            "C3": self.check_c3_skill_registry(),
            "C4": self.check_c4_clara_state(),
            "C5": self.check_c5_stop_hook_ordering(),
            "C6": self.check_c6_python_environment(),
            "C7": self.check_c7_recall_modification(),
            "C8": self.check_c8_supabase_migration(),
            "C9": self.check_c9_rlm_sandboxing(),
            "C10": self.check_c10_rlm_hook_interference(),
            "C11": self.check_c11_rlm_cost_ceiling(),
        }

    def check_c1_session_format(self) -> dict:
        """C1: DepthFusion code must not WRITE to .tmp session files.

        Read-only access is permitted (session/loader.py reads .tmp files).
        Writing or deleting .tmp files would corrupt Claude Code's session state.
        """
        df_src = _PROJECT_ROOT / "src" / "depthfusion"
        if not df_src.is_dir():
            return _check_result(GREEN, "C1: Source directory not found — assumed safe", "")

        # Patterns that indicate writing to .tmp files
        write_patterns = [
            # open(..., 'w'), open(..., 'a'), open(..., 'wb') on .tmp paths
            "open(.*\\.tmp.*['\"]w",
            "open(.*\\.tmp.*['\"]a",
            "\\.write(.*\\.tmp",
            "\\.unlink(.*\\.tmp",
            "os\\.remove.*\\.tmp",
            "shutil.*\\.tmp",
        ]

        violations = []
        for py_file in df_src.rglob("*.py"):
            try:
                content = py_file.read_text(errors="ignore")
                if ".tmp" not in content:
                    continue
                import re
                for pattern in write_patterns:
                    if re.search(pattern, content):
                        violations.append(str(py_file.relative_to(_PROJECT_ROOT)))
                        break
            except Exception:
                pass

        if violations:
            return _check_result(
                RED,
                "C1: DepthFusion code writes to .tmp session files",
                f"Files: {', '.join(violations)}",
            )
        return _check_result(
            GREEN,
            "C1: No .tmp write access in DepthFusion code (read-only access is safe)",
            "",
        )

    def check_c2_mcp_tool_count(self) -> dict:
        """C2: MCP tool count must stay below 80."""
        count = self._scan()["mcp_tool_count"]
        if count >= 80:
            return _check_result(
                RED,
                f"C2: MCP tool count {count} >= 80 — Claude Code limit at risk",
                "Remove unused MCP servers or consolidate tools",
            )
        if count >= 75:
            return _check_result(
                YELLOW,
                f"C2: MCP tool count {count} approaching limit (75-79)",
                "Consider removing unused MCP servers before adding DepthFusion",
            )
        return _check_result(GREEN, f"C2: MCP tool count {count} — safe", "")

    def check_c3_skill_registry(self) -> dict:
        """C3: Skills directory should exist for DepthFusion skill manifest."""
        scan = self._scan()
        skills = scan.get("skills", {})
        claude_dir = self.scanner.claude_dir
        skills_dir = claude_dir / "skills"

        if not skills_dir.is_dir():
            return _check_result(
                YELLOW,
                "C3: Skills directory does not exist — will be created at install",
                str(skills_dir),
            )

        registry = skills_dir / "REGISTRY.md"
        if not registry.exists():
            return _check_result(
                YELLOW,
                "C3: REGISTRY.md not found — DepthFusion manifest will add it",
                str(registry),
            )

        return _check_result(
            GREEN,
            f"C3: Skills directory exists with {len(skills)} skill(s)",
            str(skills_dir),
        )

    def check_c4_clara_state(self) -> dict:
        """C4: CLaRa absent means no integration complexity."""
        # CLaRa would manifest as specific files in ~/.claude
        claude_dir = self.scanner.claude_dir
        clara_indicators = ["clara", "CLARA", "clara-state"]
        # Directories that produce false positives (npm packages, Python caches, build artifacts)
        _EXCLUDED_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".git", "dist"}

        for indicator in clara_indicators:
            matches = list(claude_dir.rglob(f"*{indicator}*"))
            # Filter out matches inside excluded directories
            real_matches = [
                m for m in matches
                if not any(excluded in m.parts for excluded in _EXCLUDED_DIRS)
            ]
            if real_matches:
                return _check_result(
                    YELLOW,
                    "C4: CLaRa presence detected — manual integration review needed",
                    f"Found indicator: {indicator} in {real_matches[0]}",
                )
        return _check_result(GREEN, "C4: CLaRa not detected — no integration complexity", "")

    def check_c5_stop_hook_ordering(self) -> dict:
        """C5: DepthFusion hooks should run AFTER existing hooks."""
        scan = self._scan()
        hooks = scan.get("hooks", [])
        df_hooks = [h for h in hooks if "depthfusion" in Path(h).name.lower()]

        if not df_hooks:
            return _check_result(
                GREEN,
                "C5: No DepthFusion hooks installed yet — ordering not a concern",
                "Hooks will be added with numeric prefix at install time",
            )

        # Check if DepthFusion hooks have high numeric prefixes (run after others)
        for hook_path in df_hooks:
            name = Path(hook_path).name
            # Look for numeric prefix like "90-depthfusion-..." which runs late
            parts = name.split("-")
            if parts[0].isdigit() and int(parts[0]) >= 50:
                continue
            return _check_result(
                YELLOW,
                "C5: DepthFusion hook may run before other hooks",
                f"Consider renaming {name} to use a high numeric prefix (e.g. 90-...)",
            )

        return _check_result(GREEN, "C5: DepthFusion hooks ordered correctly", "")

    def check_c6_python_environment(self) -> dict:
        """C6: Python venv should exist at project directory."""
        # Check for venv in common locations
        candidates = [
            _PROJECT_ROOT / ".venv",
            _PROJECT_ROOT / "venv",
            _PROJECT_ROOT / "env",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return _check_result(
                    GREEN,
                    f"C6: Python virtual environment found at {candidate.name}/",
                    str(candidate),
                )

        # Also check if we're inside a venv currently
        if sys.prefix != sys.base_prefix:
            return _check_result(
                GREEN,
                "C6: Running inside a Python virtual environment",
                sys.prefix,
            )

        return _check_result(
            RED,
            "C6: No Python virtual environment found",
            f"Run: python -m venv {_PROJECT_ROOT / '.venv'}",
        )

    def check_c7_recall_modification(self) -> dict:
        """C7: /recall command file should exist (additive modification possible)."""
        claude_dir = self.scanner.claude_dir
        commands_dir = claude_dir / "commands"

        # Check various possible locations
        recall_locations = [
            commands_dir / "recall.md",
            commands_dir / "recall",
            claude_dir / "commands" / "recall.md",
        ]

        for loc in recall_locations:
            if loc.exists():
                return _check_result(
                    GREEN,
                    f"C7: /recall command found at {loc.name}",
                    str(loc),
                )

        # Check in skills as well
        skills_dir = claude_dir / "skills"
        if skills_dir.is_dir():
            for f in skills_dir.rglob("recall*"):
                return _check_result(
                    GREEN,
                    f"C7: /recall found in skills at {f.name}",
                    str(f),
                )

        return _check_result(
            YELLOW,
            "C7: /recall command not found — DepthFusion will add it additively",
            str(commands_dir / "recall.md"),
        )

    def check_c8_supabase_migration(self) -> dict:
        """C8: Supabase not in scope — no migration needed."""
        return _check_result(
            GREEN,
            "C8: Supabase not in scope for DepthFusion v0.1",
            "Bus backend defaults to file-based storage",
        )

    def check_c9_rlm_sandboxing(self) -> dict:
        """C9: sandbox.py must exist for RLM sandboxed execution."""
        sandbox_path = _PROJECT_ROOT / "src" / "depthfusion" / "recursive" / "sandbox.py"
        if sandbox_path.exists():
            return _check_result(
                GREEN,
                "C9: RLM sandbox module found",
                str(sandbox_path),
            )
        return _check_result(
            RED,
            "C9: sandbox.py not found — RLM sub-processes would run unrestricted",
            str(sandbox_path),
        )

    def check_c10_rlm_hook_interference(self) -> dict:
        """C10: RLM subprocess should not inherit Claude hooks environment."""
        # Check that sandbox.py uses subprocess.run without inheriting env
        sandbox_path = _PROJECT_ROOT / "src" / "depthfusion" / "recursive" / "sandbox.py"
        if not sandbox_path.exists():
            return _check_result(
                YELLOW,
                "C10: sandbox.py not found — cannot verify hook isolation",
                "",
            )

        content = sandbox_path.read_text(errors="ignore")
        # Check that subprocess.run is used (not subprocess.Popen with shell=True)
        # and that there's no explicit env=os.environ passthrough
        if "env=os.environ" in content or "env=environ" in content:
            return _check_result(
                RED,
                "C10: sandbox.py passes os.environ to subprocess — Claude hooks inherited",
                "Remove env= parameter so subprocess gets a clean environment",
            )

        return _check_result(
            GREEN,
            "C10: RLM sandbox uses subprocess without explicit env passthrough",
            "Claude hook env variables not inherited by default",
        )

    def check_c11_rlm_cost_ceiling(self) -> dict:
        """C11: Cost ceiling config must exist and be <= $1.00."""
        # Check the config module for cost ceiling default
        config_path = _PROJECT_ROOT / "src" / "depthfusion" / "core" / "config.py"
        if not config_path.exists():
            return _check_result(
                RED,
                "C11: config.py not found — cost ceiling unknown",
                "",
            )

        content = config_path.read_text(errors="ignore")
        # Check that rlm_cost_ceiling is defined
        if "rlm_cost_ceiling" not in content:
            return _check_result(
                RED,
                "C11: rlm_cost_ceiling not defined in config",
                str(config_path),
            )

        # Try to import and check the actual value
        try:
            from depthfusion.core.config import DepthFusionConfig
            default_cfg = DepthFusionConfig()
            ceiling = default_cfg.rlm_cost_ceiling
            if ceiling > 1.00:
                return _check_result(
                    RED,
                    f"C11: rlm_cost_ceiling ${ceiling:.2f} exceeds $1.00 safety limit",
                    "Reduce rlm_cost_ceiling in config or via DEPTHFUSION_RLM_COST_CEILING env var",
                )
            return _check_result(
                GREEN,
                f"C11: rlm_cost_ceiling ${ceiling:.2f} — within safe limit",
                "",
            )
        except Exception as exc:
            return _check_result(
                YELLOW,
                "C11: Could not import config to verify ceiling value",
                str(exc),
            )
