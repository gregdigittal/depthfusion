"""Query data readers for DepthFusion REST query endpoints.

Provides filtered, cursor-paginated access to discovery files and session
(recall) events without modifying or indexing the underlying storage.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"
_METRICS_DIR = Path.home() / ".claude" / "depthfusion-metrics"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


# ---------------------------------------------------------------------------
# Cursor helpers (opaque base64-encoded integer offset)
# ---------------------------------------------------------------------------

def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Discovery readers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML-like frontmatter from a markdown file."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        import yaml  # optional dep; safe on this host
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


def _discovery_record(filepath: Path) -> dict[str, Any]:
    """Parse a discovery file into a summary record."""
    stat = filepath.stat()
    text = filepath.read_text(errors="replace")
    fm = _parse_frontmatter(text)

    # Strip frontmatter from content preview
    body = _FRONTMATTER_RE.sub("", text).strip()
    preview = body[:300].replace("\n", " ") if body else ""

    # Normalise date: prefer frontmatter `date`, else derive from filename.
    # PyYAML parses bare YYYY-MM-DD as datetime.date; convert to str.
    raw_date = fm.get("date")
    if raw_date is not None:
        date_str = str(raw_date)  # handles datetime.date and string
    else:
        date_str = ""
    if not date_str:
        name = filepath.stem  # e.g. 2026-05-13-depthfusion-foo
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        date_str = m.group(1) if m else ""

    tags_raw = fm.get("tags", "")
    if isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw]
    elif isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = []

    return {
        "filename": filepath.name,
        "date": date_str,
        "project": str(fm.get("project", "")),
        "agent": str(fm.get("agent", "")),
        "tags": tags,
        "title": filepath.stem,
        "content_preview": preview,
        "file_path": str(filepath),
        "size_bytes": stat.st_size,
    }


def query_discoveries(
    project: Optional[str] = None,
    agent: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    tags: Optional[list[str]] = None,
    cursor: Optional[str] = None,
    limit: int = 100,
    discoveries_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Return paginated discovery records matching filters."""
    base = discoveries_dir or _DISCOVERIES_DIR
    pattern = str(base / "*.md")
    all_files = sorted(glob.glob(pattern))

    records: list[dict] = []
    for fp in all_files:
        try:
            rec = _discovery_record(Path(fp))
        except Exception:
            continue

        # Date filters
        date_str = rec["date"]
        if from_dt and date_str:
            try:
                rd = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                if rd < from_dt:
                    continue
            except ValueError:
                pass
        if to_dt and date_str:
            try:
                rd = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                if rd > to_dt:
                    continue
            except ValueError:
                pass

        if project and rec["project"] != project:
            continue
        if agent and rec["agent"] != agent:
            continue
        if tags:
            rec_tags = set(rec["tags"])
            if not all(t in rec_tags for t in tags):
                continue

        records.append(rec)

    offset = _decode_cursor(cursor)
    page = records[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(next_offset) if next_offset < len(records) else None

    return {
        "items": page,
        "total": len(records),
        "count": len(page),
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Session (recall) readers
# ---------------------------------------------------------------------------

def _recall_files_in_range(
    metrics_dir: Path,
    from_dt: Optional[datetime],
    to_dt: Optional[datetime],
) -> list[Path]:
    """Return sorted recall JSONL files whose date prefix falls in range."""
    pattern = str(metrics_dir / "*-recall.jsonl")
    files = []
    for fp in sorted(glob.glob(pattern)):
        name = Path(fp).stem  # e.g. 2026-05-13-recall
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        if not m:
            continue
        try:
            file_date = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if from_dt and file_date.date() < from_dt.date():
            continue
        if to_dt and file_date.date() > to_dt.date():
            continue
        files.append(Path(fp))
    return files


def query_sessions(
    project: Optional[str] = None,
    agent: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    cursor: Optional[str] = None,
    limit: int = 100,
    metrics_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Return paginated recall session event records matching filters."""
    base = metrics_dir or _METRICS_DIR
    recall_files = _recall_files_in_range(base, from_dt, to_dt)

    all_events: list[dict] = []
    for fp in recall_files:
        try:
            for line in fp.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Timestamp filter
                ts_str = ev.get("timestamp", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if from_dt and ts < from_dt:
                            continue
                        if to_dt and ts > to_dt:
                            continue
                    except ValueError:
                        pass
                # Project filter: recall events carry config_version_id but no
                # project field; `agent` maps to `mode` (best-effort).
                if agent:
                    ev_mode = ev.get("mode", "")
                    if ev_mode != agent:
                        continue
                all_events.append(ev)
        except Exception:
            continue

    offset = _decode_cursor(cursor)
    page = all_events[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(next_offset) if next_offset < len(all_events) else None

    return {
        "items": page,
        "total": len(all_events),
        "count": len(page),
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def query_aggregate(
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    metrics_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Return aggregated statistics over recall session events."""
    result = query_sessions(
        from_dt=from_dt,
        to_dt=to_dt,
        limit=10_000,
        metrics_dir=metrics_dir,
    )
    events = result["items"]
    if not events:
        return {
            "total_events": 0,
            "total_latency_ms": 0.0,
            "avg_latency_ms": None,
            "p95_latency_ms": None,
            "avg_result_count": None,
            "modes": {},
            "config_versions": {},
        }

    latencies = sorted(
        [e["total_latency_ms"] for e in events if "total_latency_ms" in e]
    )
    p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) > 20 else (latencies[-1] if latencies else None)

    modes: dict[str, int] = {}
    versions: dict[str, int] = {}
    result_counts: list[int] = []
    for ev in events:
        m = ev.get("mode", "unknown")
        modes[m] = modes.get(m, 0) + 1
        v = ev.get("config_version_id", "")
        if v:
            versions[v] = versions.get(v, 0) + 1
        rc = ev.get("result_count")
        if rc is not None:
            result_counts.append(rc)

    total_lat = sum(latencies)
    avg_lat = total_lat / len(latencies) if latencies else None
    avg_rc = sum(result_counts) / len(result_counts) if result_counts else None

    return {
        "total_events": len(events),
        "total_latency_ms": round(total_lat, 3),
        "avg_latency_ms": round(avg_lat, 3) if avg_lat is not None else None,
        "p95_latency_ms": round(p95, 3) if p95 is not None else None,
        "avg_result_count": round(avg_rc, 2) if avg_rc is not None else None,
        "modes": modes,
        "config_versions": versions,
    }
