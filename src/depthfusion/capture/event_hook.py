"""S-73: high-importance discovery event emitter.

Appends a JSONL line to a rotating daily log file whenever a discovery
is published with importance >= threshold.  Non-critical path — all I/O
errors are swallowed so a broken event log never blocks a publish.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from depthfusion.core.types import ContextItem

_DEFAULT_EVENT_LOG = "~/.claude/shared/depthfusion-events.jsonl"
_DEFAULT_THRESHOLD = 0.8


def emit_if_high_importance(
    item: "ContextItem",
    *,
    event_log: str = _DEFAULT_EVENT_LOG,
    threshold: float = _DEFAULT_THRESHOLD,
) -> bool:
    """Emit a JSONL event when item.importance >= threshold.

    Returns True if an event was written, False otherwise (including on I/O error).
    Daily rotation: physical file is ``{stem}-YYYY-MM-DD{ext}`` derived from
    the configured path so the logical path stays stable for consumers.
    """
    importance: float = item.importance or 0.0
    if importance < threshold:
        return False

    salience: float = item.salience or 0.0
    metadata: dict[str, Any] = item.metadata or {}
    content: str = item.content or ""

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "high_importance_discovery",
        "project": metadata.get("project", item.source_agent),
        "file_path": metadata.get("file_path", ""),
        "importance": importance,
        "salience": salience,
        "summary": metadata.get("summary", content[:500]),
    }

    base = Path(os.path.expanduser(event_log))
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rotated = base.parent / f"{base.stem}-{date_str}{base.suffix or '.jsonl'}"

    try:
        rotated.parent.mkdir(parents=True, exist_ok=True)
        with rotated.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        return False

    return True
