"""Offline cache hit-rate telemetry — local only (E-58 S-189 T-656).

Tracks cache hit/miss events on-device and computes the offline hit rate.
All telemetry data is stored locally in a SQLite database; no data is ever
uploaded to a remote service.

Design rules
------------
* **Local only** — no network imports, no remote I/O.
* **Simple accounting** — record hit/miss events with a timestamp; compute
  rate over a rolling window or the full history.
* **Dogfood report** — ``generate_report()`` returns a human-readable Markdown
  string suitable for writing to ``docs/runbooks/dogfood-reports/``.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "HitRateStore",
    "HitRateReport",
    "generate_report",
]

_PRIVACY_GUARD: str = "on-device-only-never-upload"


@dataclass
class HitRateReport:
    """Summary statistics from the local hit-rate store.

    Attributes
    ----------
    total_hits:
        Number of cache-hit events recorded.
    total_misses:
        Number of cache-miss events recorded.
    hit_rate:
        ``total_hits / (total_hits + total_misses)``, or 0.0 if no events.
    window_seconds:
        The time window in seconds used to compute these statistics,
        or None for all-time.
    generated_at:
        Unix timestamp when the report was generated.
    """

    total_hits: int = 0
    total_misses: int = 0
    hit_rate: float = 0.0
    window_seconds: Optional[float] = None
    generated_at: float = field(default_factory=time.time)

    @property
    def total_lookups(self) -> int:
        """Total number of cache lookups (hits + misses)."""
        return self.total_hits + self.total_misses


class HitRateStore:
    """Persists cache hit/miss events on-device in a SQLite database.

    Parameters
    ----------
    db_path:
        Filesystem path for the SQLite database.  Pass ``":memory:"`` for
        in-process storage (useful in tests).
    """

    upload_disabled: bool = True  # INVARIANT: signals are never uploaded

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()

    # -----------------------------------------------------------------------
    # Schema
    # -----------------------------------------------------------------------

    def _bootstrap(self) -> None:
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_events (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    hit     INTEGER NOT NULL,   -- 1 = hit, 0 = miss
                    path    TEXT,
                    ts      REAL    NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ce_ts "
                "ON cache_events (ts DESC)"
            )
            self._conn.commit()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def record_hit(
        self,
        path: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """Record a cache hit event."""
        self._record(hit=True, path=path, now=now)

    def record_miss(
        self,
        path: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """Record a cache miss event."""
        self._record(hit=False, path=path, now=now)

    def compute(
        self,
        window_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> HitRateReport:
        """Compute hit-rate statistics.

        Parameters
        ----------
        window_seconds:
            If given, only consider events within the last *window_seconds*
            seconds.  If None, uses all recorded events.
        now:
            Override the current time (useful for deterministic testing).
        """
        ts_now = now if now is not None else time.time()

        conditions: list[str] = []
        params: list[object] = []
        if window_seconds is not None:
            cutoff = ts_now - window_seconds
            conditions.append("ts >= ?")
            params.append(cutoff)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                f"SELECT "
                f"  COALESCE(SUM(hit), 0) AS hits, "
                f"  COALESCE(COUNT(*) - SUM(hit), 0) AS misses "
                f"FROM cache_events {where}",
                params,
            ).fetchone()

        hits = int(row["hits"])
        misses = int(row["misses"])
        total = hits + misses
        hit_rate = (hits / total) if total > 0 else 0.0

        return HitRateReport(
            total_hits=hits,
            total_misses=misses,
            hit_rate=hit_rate,
            window_seconds=window_seconds,
            generated_at=ts_now,
        )

    def total_events(self) -> int:
        """Return the total number of recorded events."""
        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT COUNT(*) AS cnt FROM cache_events"
            ).fetchone()
        return int(row["cnt"])

    def clear(self) -> None:
        """Remove all recorded events."""
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM cache_events")
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _record(
        self,
        hit: bool,
        path: Optional[str],
        now: Optional[float],
    ) -> None:
        ts = now if now is not None else time.time()
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO cache_events (hit, path, ts) VALUES (?, ?, ?)",
                (1 if hit else 0, path, ts),
            )
            self._conn.commit()


def generate_report(
    store: HitRateStore,
    window_seconds: Optional[float] = None,
    now: Optional[float] = None,
    title: str = "Offline Cache Hit-Rate Dogfood Report",
) -> str:
    """Generate a Markdown dogfood report from *store*.

    Parameters
    ----------
    store:
        The ``HitRateStore`` to report on.
    window_seconds:
        If given, restricts statistics to the last *window_seconds* seconds.
    now:
        Override the current time.
    title:
        Report title (used in the Markdown heading).

    Returns
    -------
    str
        A Markdown-formatted report suitable for writing to
        ``docs/runbooks/dogfood-reports/``.
    """
    ts_now = now if now is not None else time.time()
    report = store.compute(window_seconds=window_seconds, now=ts_now)

    import datetime

    generated_dt = datetime.datetime.fromtimestamp(
        report.generated_at, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    window_desc = (
        f"Last {window_seconds / 3600:.1f} hours"
        if window_seconds is not None
        else "All time"
    )

    target_met = report.hit_rate >= 0.80
    status_emoji = "PASS" if target_met else "FAIL"

    lines = [
        f"# {title}",
        "",
        f"**Generated:** {generated_dt}  ",
        f"**Window:** {window_desc}  ",
        "**Privacy:** on-device telemetry only — data is never uploaded",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total lookups | {report.total_lookups:,} |",
        f"| Cache hits | {report.total_hits:,} |",
        f"| Cache misses | {report.total_misses:,} |",
        f"| Hit rate | {report.hit_rate:.1%} |",
        f"| Target (≥ 80%) | {status_emoji} |",
        "",
        "## Notes",
        "",
        "- Hit-rate telemetry is stored in a local SQLite database.",
        "- No data is uploaded to any remote service.",
        "- Signals are collected via `HitRateStore.record_hit()` / `.record_miss()`.",
        "- The 80% target is the dogfood goal from S-189 AC-3.",
        "",
    ]

    if report.total_lookups == 0:
        lines += [
            "## Warning",
            "",
            "No cache events have been recorded yet.  "
            "Start using the app in offline mode to generate telemetry.",
            "",
        ]

    return "\n".join(lines)
