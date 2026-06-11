"""Query data readers for DepthFusion REST query endpoints.

Provides filtered, cursor-paginated access to discovery files and session
(recall) events without modifying or indexing the underlying storage.

T-575: All query functions accept a ``principal`` argument.  Discovery records
are trimmed to those the principal can see (acl_allow check via
:mod:`depthfusion.authz.frontmatter`).  Session / aggregate data carries no
per-record ACL today, so the principal is accepted but not further filtered —
callers are expected to supply it so future column-level trimming can be added
without a signature change.
"""
from __future__ import annotations

import base64
import glob
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from depthfusion.identity.models import Principal

_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"
_METRICS_DIR = Path.home() / ".claude" / "depthfusion-metrics"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


# ---------------------------------------------------------------------------
# Cursor helpers (opaque base64-encoded integer offset)
# ---------------------------------------------------------------------------

def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[int]:
    """Decode cursor; returns None if cursor is non-empty but invalid."""
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ACL helper
# ---------------------------------------------------------------------------

def _principal_can_see(content: str, principal: "Optional[Principal]") -> bool:
    """Return True if *principal* is allowed to read a discovery file.

    When *principal* is None (unauthenticated / no trimming requested) we
    allow access — callers are responsible for ensuring a principal is always
    supplied on authenticated routes.

    The check reads ``acl_allow`` from the document's YAML frontmatter via
    :func:`depthfusion.authz.frontmatter.parse_acl`.  When frontmatter is
    absent the default from ``parse_acl`` applies (``acl_allow=["greg"]``,
    ``classification=internal``).

    Allowed when at least one of ``principal.principal_id`` or any item in
    ``principal.groups`` appears in the parsed ``acl_allow`` list.
    """
    if principal is None:
        return True

    try:
        from depthfusion.authz.frontmatter import parse_acl
        acl = parse_acl(content)
    except Exception:
        # Malformed ACL frontmatter → fail-closed.
        return False

    allowed_ids: set[str] = {principal.principal_id}
    for g in (principal.groups or []):
        allowed_ids.add(g)

    return bool(set(acl.acl_allow) & allowed_ids)


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
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return paginated discovery records matching filters.

    T-575: only records visible to *principal* (via acl_allow frontmatter) are
    included.  When *principal* is None, no ACL trimming is applied — this is
    intentional for the legacy/internal path; callers on authenticated routes
    MUST supply the principal.
    """
    base = discoveries_dir or _DISCOVERIES_DIR
    pattern = str(base / "*.md")
    all_files = sorted(glob.glob(pattern))

    records: list[dict] = []
    for fp in all_files:
        try:
            path = Path(fp)
            # ACL check — read full text once; pass to both _principal_can_see
            # and _discovery_record (which re-reads; acceptable for filesystem).
            content = path.read_text(errors="replace")
            if not _principal_can_see(content, principal):
                continue

            rec = _discovery_record(path)
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
    if offset is None:
        raise ValueError("invalid_cursor")
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
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return paginated recall session event records matching filters.

    T-575: *principal* is accepted and threaded through for future per-row
    trimming.  Session JSONL files do not carry per-record acl_allow today so
    no rows are filtered beyond what the authenticated route gate already
    enforces.
    """
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
    if offset is None:
        raise ValueError("invalid_cursor")
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
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return aggregated statistics over recall session events.

    Streams directly from JSONL files — does not materialise all events in
    memory. The latency p95 sample list is capped at 100k values to avoid
    unbounded memory growth on very large datasets.

    T-575: *principal* is accepted for API consistency and future trimming.
    Aggregate statistics are computed over the records the caller can see; the
    session JSONL format does not carry per-record ACL today so no rows are
    dropped by principal checks here.
    """
    base = metrics_dir or _METRICS_DIR
    recall_files = _recall_files_in_range(base, from_dt, to_dt)

    total = 0
    modes: dict[str, int] = {}
    versions: dict[str, int] = {}
    latencies: list[float] = []
    result_counts: list[int] = []
    _LAT_SAMPLE_CAP = 100_000

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
                total += 1
                m = ev.get("mode", "unknown")
                modes[m] = modes.get(m, 0) + 1
                v = ev.get("config_version_id", "")
                if v:
                    versions[v] = versions.get(v, 0) + 1
                lat = ev.get("total_latency_ms")
                if lat is not None and len(latencies) < _LAT_SAMPLE_CAP:
                    latencies.append(lat)
                rc = ev.get("result_count")
                if rc is not None:
                    result_counts.append(rc)
        except Exception:
            continue

    if total == 0:
        return {
            "total_events": 0,
            "total_latency_ms": 0.0,
            "avg_latency_ms": None,
            "p95_latency_ms": None,
            "avg_result_count": None,
            "modes": {},
            "config_versions": {},
        }

    latencies.sort()
    p95: Optional[float] = None
    if latencies:
        p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) > 20 else latencies[-1]

    total_lat = sum(latencies)
    avg_lat: Optional[float] = total_lat / len(latencies) if latencies else None
    avg_rc: Optional[float] = sum(result_counts) / len(result_counts) if result_counts else None

    return {
        "total_events": total,
        "total_latency_ms": round(total_lat, 3),
        "avg_latency_ms": round(avg_lat, 3) if avg_lat is not None else None,
        "p95_latency_ms": round(p95, 3) if p95 is not None else None,
        "avg_result_count": round(avg_rc, 2) if avg_rc is not None else None,
        "modes": modes,
        "config_versions": versions,
    }
