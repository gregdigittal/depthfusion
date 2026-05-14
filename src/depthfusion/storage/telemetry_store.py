"""SQLite-backed store for per-tool-call telemetry events (E-33 S-106)."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DDL = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL,
    session_type     TEXT    NOT NULL DEFAULT 'agent',
    agent            TEXT    NOT NULL DEFAULT '',
    project          TEXT    NOT NULL DEFAULT '',
    story_id         TEXT    NOT NULL DEFAULT '',
    sprint           TEXT    NOT NULL DEFAULT '',
    tool_name        TEXT    NOT NULL,
    duration_ms      REAL,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    cost_usd_estimate REAL,
    recorded_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tel_session      ON telemetry_events(session_id);
CREATE INDEX IF NOT EXISTS idx_tel_session_type ON telemetry_events(session_type);
CREATE INDEX IF NOT EXISTS idx_tel_project      ON telemetry_events(project);
CREATE INDEX IF NOT EXISTS idx_tel_agent        ON telemetry_events(agent);
CREATE INDEX IF NOT EXISTS idx_tel_tool         ON telemetry_events(tool_name);
CREATE INDEX IF NOT EXISTS idx_tel_recorded     ON telemetry_events(recorded_at);
"""

# Migrate existing DBs that predate the session_type column (S-107).
_MIGRATION = "ALTER TABLE telemetry_events ADD COLUMN session_type TEXT NOT NULL DEFAULT 'agent'"


class TelemetryStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        # Migrate pre-S-107 DBs that lack session_type column.
        try:
            self._conn.execute(_MIGRATION)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists.
        self._conn.commit()

    def record(
        self,
        session_id: str,
        tool_name: str,
        *,
        session_type: str = "agent",
        agent: str = "",
        project: str = "",
        story_id: str = "",
        sprint: str = "",
        duration_ms: Optional[float] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        cost_usd_estimate: Optional[float] = None,
        recorded_at: Optional[str] = None,
    ) -> int:
        """Insert one telemetry event and return its row id."""
        ts = recorded_at or datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO telemetry_events
                   (session_id, session_type, agent, project, story_id, sprint,
                    tool_name, duration_ms, tokens_in, tokens_out,
                    cost_usd_estimate, recorded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    session_type,
                    agent,
                    project,
                    story_id,
                    sprint,
                    tool_name,
                    duration_ms,
                    tokens_in,
                    tokens_out,
                    cost_usd_estimate,
                    ts,
                ),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def query(
        self,
        project: Optional[str] = None,
        agent: Optional[str] = None,
        session_type: Optional[str] = None,
        story_id: Optional[str] = None,
        sprint: Optional[str] = None,
        tool_name: Optional[str] = None,
        from_dt: Optional[str] = None,
        to_dt: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return matching telemetry rows as dicts, ordered by recorded_at desc."""
        clauses: list[str] = []
        params: list[Any] = []

        if project:
            clauses.append("project = ?")
            params.append(project)
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        if session_type:
            clauses.append("session_type = ?")
            params.append(session_type)
        if story_id:
            clauses.append("story_id = ?")
            params.append(story_id)
        if sprint:
            clauses.append("sprint = ?")
            params.append(sprint)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if from_dt:
            clauses.append("recorded_at >= ?")
            params.append(from_dt)
        if to_dt:
            clauses.append("recorded_at <= ?")
            params.append(to_dt)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT id, session_id, session_type, agent, project,
                           story_id, sprint, tool_name, duration_ms, tokens_in,
                           tokens_out, cost_usd_estimate, recorded_at
                    FROM telemetry_events {where}
                    ORDER BY recorded_at DESC LIMIT ? OFFSET ?""",
                params,
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def aggregate(
        self,
        project: Optional[str] = None,
        agent: Optional[str] = None,
        session_type: Optional[str] = None,
        story_id: Optional[str] = None,
        sprint: Optional[str] = None,
        period: Optional[str] = None,
        from_dt: Optional[str] = None,
        to_dt: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return aggregated stats grouped by the supplied dimensions.

        `period` can be 'day', 'week', 'month' — adds a strftime bucketing
        column to the result.  When omitted, a single summary row is returned.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if project:
            clauses.append("project = ?")
            params.append(project)
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        if session_type:
            clauses.append("session_type = ?")
            params.append(session_type)
        if story_id:
            clauses.append("story_id = ?")
            params.append(story_id)
        if sprint:
            clauses.append("sprint = ?")
            params.append(sprint)
        if from_dt:
            clauses.append("recorded_at >= ?")
            params.append(from_dt)
        if to_dt:
            clauses.append("recorded_at <= ?")
            params.append(to_dt)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        period_fmt = {
            "day": "%Y-%m-%d",
            "week": "%Y-W%W",
            "month": "%Y-%m",
        }.get(period or "", None)

        if period_fmt:
            group_col = f"strftime('{period_fmt}', recorded_at)"
            select_extra = f"{group_col} AS period,"
            group_by = f"GROUP BY {group_col}"
        else:
            select_extra = "'all' AS period,"
            group_by = ""

        with self._lock:
            cur = self._conn.execute(
                f"""SELECT {select_extra}
                           COUNT(*) AS event_count,
                           COUNT(DISTINCT session_id) AS session_count,
                           SUM(duration_ms) AS total_duration_ms,
                           AVG(duration_ms) AS avg_duration_ms,
                           SUM(tokens_in) AS total_tokens_in,
                           SUM(tokens_out) AS total_tokens_out,
                           SUM(cost_usd_estimate) AS total_cost_usd
                    FROM telemetry_events {where}
                    {group_by}
                    ORDER BY period""",
                params,
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        return {"rows": rows, "row_count": len(rows)}


def compute_think_times(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate telemetry rows with inter-tool think_time_ms for human sessions.

    For each consecutive pair in the same session (ordered by recorded_at),
    think_time_ms = gap between end of previous tool call and start of the next.
    The first event in a session has think_time_ms=None.

    Only meaningful for session_type='human'. Operates on the output of
    TelemetryStore.query() — dicts must have recorded_at (ISO-8601 str) and
    optionally duration_ms.
    """
    from datetime import datetime, timezone

    def _parse(ts: str) -> datetime:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    by_session: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_session.setdefault(ev["session_id"], []).append(ev)

    result: list[dict[str, Any]] = []
    for session_events in by_session.values():
        ordered = sorted(session_events, key=lambda e: e["recorded_at"])
        for i, ev in enumerate(ordered):
            ev = dict(ev)
            if i == 0:
                ev["think_time_ms"] = None
            else:
                prev = ordered[i - 1]
                try:
                    prev_start = _parse(prev["recorded_at"])
                    prev_dur = float(prev.get("duration_ms") or 0)
                    prev_end_ms = prev_start.timestamp() * 1000 + prev_dur
                    cur_start_ms = _parse(ev["recorded_at"]).timestamp() * 1000
                    gap = max(0.0, cur_start_ms - prev_end_ms)
                    ev["think_time_ms"] = round(gap, 1)
                except (ValueError, TypeError):
                    ev["think_time_ms"] = None
            result.append(ev)
    return result
