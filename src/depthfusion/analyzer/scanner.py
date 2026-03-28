"""InstanceScanner — scans the Claude Code installation at ~/.claude/."""
from __future__ import annotations

import subprocess
from pathlib import Path


class InstanceScanner:
    """Scans the Claude Code installation at ~/.claude/ and reports what exists."""

    def __init__(self, claude_dir: Path | None = None) -> None:
        self.claude_dir = claude_dir or Path.home() / ".claude"

    def scan(self) -> dict:
        """Return dict describing what exists in the Claude Code installation.

        Keys:
        - hooks: list of hook script paths found
        - commands: list of command files found
        - skills: dict of skill name -> path
        - sessions: list of session file paths (*.tmp)
        - memory_files: list of memory file paths
        - mcp_tool_count: int (count of registered MCP servers from claude mcp list)
        - depthfusion_installed: bool
        """
        hooks = self._scan_hooks()
        commands = self._scan_commands()
        skills = self._scan_skills()
        sessions = self._scan_sessions()
        memory_files = self._scan_memory_files()
        tool_count = self.mcp_tool_count()
        depthfusion_installed = self._check_depthfusion_installed()

        return {
            "hooks": hooks,
            "commands": commands,
            "skills": skills,
            "sessions": sessions,
            "memory_files": memory_files,
            "mcp_tool_count": tool_count,
            "depthfusion_installed": depthfusion_installed,
        }

    def _scan_hooks(self) -> list[str]:
        hooks_dir = self.claude_dir / "hooks"
        if not hooks_dir.is_dir():
            return []
        return [str(p) for p in hooks_dir.iterdir() if p.is_file()]

    def _scan_commands(self) -> list[str]:
        commands_dir = self.claude_dir / "commands"
        if not commands_dir.is_dir():
            return []
        return [str(p) for p in commands_dir.iterdir() if p.is_file()]

    def _scan_skills(self) -> dict[str, str]:
        skills_dir = self.claude_dir / "skills"
        if not skills_dir.is_dir():
            return {}
        result: dict[str, str] = {}
        for entry in skills_dir.iterdir():
            if entry.is_dir():
                result[entry.name] = str(entry)
            elif entry.is_file():
                result[entry.stem] = str(entry)
        return result

    def _scan_sessions(self) -> list[str]:
        sessions_dir = self.claude_dir / "sessions"
        if not sessions_dir.is_dir():
            return []
        return [str(p) for p in sessions_dir.glob("*.tmp")]

    def _scan_memory_files(self) -> list[str]:
        memory_dir = self.claude_dir / "memory"
        if not memory_dir.is_dir():
            return []
        return [str(p) for p in memory_dir.iterdir() if p.is_file()]

    def _check_depthfusion_installed(self) -> bool:
        # Check if depthfusion MCP server is registered in claude settings
        settings_file = self.claude_dir / "settings.json"
        if settings_file.exists():
            try:
                import json
                data = json.loads(settings_file.read_text())
                mcp_servers = data.get("mcpServers", {})
                if any("depthfusion" in k.lower() for k in mcp_servers):
                    return True
            except Exception:
                pass

        # Check for depthfusion hook files
        hooks_dir = self.claude_dir / "hooks"
        if hooks_dir.is_dir():
            for f in hooks_dir.iterdir():
                if "depthfusion" in f.name.lower():
                    return True

        # Check for depthfusion skill manifest
        skills_dir = self.claude_dir / "skills"
        if skills_dir.is_dir():
            for entry in skills_dir.iterdir():
                if "depthfusion" in entry.name.lower():
                    return True

        return False

    def mcp_tool_count(self) -> int:
        """Run `claude mcp list` and count entries. Returns 0 on failure."""
        try:
            result = subprocess.run(
                ["claude", "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return 0
            lines = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            return len(lines)
        except Exception:
            return 0
