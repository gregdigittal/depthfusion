"""Session scope configuration for cross-project graph visibility."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from depthfusion.graph.types import GraphScope

_DEFAULT_SCOPE_PATH = Path.home() / ".claude" / ".depthfusion-session-scope.json"


def default_scope(project: str, session_id: str) -> GraphScope:
    """Return a per-project (isolated) scope — the safe default."""
    return GraphScope(
        mode="project",
        active_projects=[project] if project else [],
        session_id=session_id,
        set_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    )


def read_scope(path: Path | None = None) -> GraphScope | None:
    """Read scope from JSON file. Returns None if missing or invalid."""
    target = path or _DEFAULT_SCOPE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return GraphScope(
            mode=data["mode"],
            active_projects=data.get("active_projects", []),
            session_id=data.get("session_id", ""),
            set_at=data.get("set_at", ""),
        )
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def write_scope(scope: GraphScope, path: Path | None = None) -> None:
    """Persist scope to JSON file. Creates parent directories as needed."""
    target = path or _DEFAULT_SCOPE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "mode": scope.mode,
            "active_projects": scope.active_projects,
            "session_id": scope.session_id,
            "set_at": scope.set_at,
        }, indent=2),
        encoding="utf-8",
    )
