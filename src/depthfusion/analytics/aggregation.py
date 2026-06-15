"""AggregationService — daily/weekly rollups of usage events (E-55).

Rollups are stored in the ``analytics_rollups`` table so the summary
endpoint can serve pre-computed counts without a full table scan.

``compute_rollups()`` is idempotent: re-running it overwrites existing
rollup rows for the same (principal_id, event_type, period, period_start)
via ``INSERT OR REPLACE``.
"""
from __future__ import annotations

import logging
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .store import _connect, init_db

logger = logging.getLogger(__name__)

#: Rollup granularities supported by the service.
SUPPORTED_PERIODS = frozenset({"daily", "weekly"})


def _period_bounds(period: str, reference: date) -> tuple[date, date]:
    """Return (start, end_inclusive) for a period ending on *reference*.

    ``daily``  → single day: (reference, reference)
    ``weekly`` → 7-day window ending on reference: (reference-6d, reference)
    """
    if period == "daily":
        return reference, reference
    # weekly
    return reference - timedelta(days=6), reference


class AggregationService:
    """Computes and stores pre-aggregated usage rollups.

    Parameters
    ----------
    db_path:
        Same SQLite database used by :class:`~.collector.AnalyticsCollector`.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        init_db(self._db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_rollups(
        self,
        *,
        reference_date: date | None = None,
        periods: tuple[str, ...] = ("daily", "weekly"),
    ) -> int:
        """Compute rollups for *reference_date* and write them to the DB.

        Returns the total number of rollup rows written/updated.

        Parameters
        ----------
        reference_date:
            The "today" anchor for period calculation; defaults to
            ``date.today()`` in UTC.
        periods:
            Which granularities to compute.  Defaults to both.
        """
        if reference_date is None:
            reference_date = datetime.now(tz=timezone.utc).date()

        computed_at = datetime.now(tz=timezone.utc).isoformat()
        rows_written = 0

        try:
            with closing(_connect(self._db_path)) as conn:
                # Enumerate distinct principals
                principal_rows = conn.execute(
                    "SELECT DISTINCT principal_id FROM analytics_events"
                ).fetchall()
                principals = [r[0] for r in principal_rows]

                for principal_id in principals:
                    for period in periods:
                        if period not in SUPPORTED_PERIODS:
                            continue
                        start, end = _period_bounds(period, reference_date)
                        start_ts = datetime(
                            start.year, start.month, start.day, tzinfo=timezone.utc
                        ).isoformat()
                        end_ts = datetime(
                            end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc
                        ).isoformat()

                        # Count per event_type for this principal + window
                        counts = conn.execute(
                            "SELECT event_type, COUNT(*) as cnt"
                            "  FROM analytics_events"
                            " WHERE principal_id = ?"
                            "   AND recorded_at >= ? AND recorded_at <= ?"
                            " GROUP BY event_type",
                            (principal_id, start_ts, end_ts),
                        ).fetchall()

                        for row in counts:
                            event_type, count = row[0], row[1]
                            conn.execute(
                                "INSERT OR REPLACE INTO analytics_rollups"
                                " (principal_id, event_type, period,"
                                " period_start, count, computed_at)"
                                " VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    principal_id,
                                    event_type,
                                    period,
                                    start.isoformat(),
                                    count,
                                    computed_at,
                                ),
                            )
                            rows_written += 1

                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("analytics: rollup computation failed")

        return rows_written

    def summary(
        self,
        *,
        principal_id: str,
        period_days: int = 7,
        reference_date: date | None = None,
    ) -> dict:
        """Return an aggregated usage summary for *principal_id*.

        Computes counts directly from ``analytics_events`` (not the
        rollup table) so the endpoint always reflects real-time data
        even before :meth:`compute_rollups` has been called.

        Parameters
        ----------
        principal_id:
            The principal whose metrics are being summarised.
        period_days:
            How many days to look back (1 = today only, 7 = last 7 days).
        reference_date:
            Anchor date; defaults to today in UTC.

        Returns
        -------
        dict with keys:
            ``principal_id``, ``period_days``, ``period_start``,
            ``period_end``, ``total_events``, ``by_event_type``
        """
        if reference_date is None:
            reference_date = datetime.now(tz=timezone.utc).date()

        start_date = reference_date - timedelta(days=period_days - 1)
        start_ts = datetime(
            start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc
        ).isoformat()
        end_ts = datetime(
            reference_date.year, reference_date.month, reference_date.day,
            23, 59, 59, tzinfo=timezone.utc,
        ).isoformat()

        by_type: dict[str, int] = {}
        total = 0

        try:
            with closing(_connect(self._db_path)) as conn:
                rows = conn.execute(
                    "SELECT event_type, COUNT(*) as cnt"
                    "  FROM analytics_events"
                    " WHERE principal_id = ?"
                    "   AND recorded_at >= ? AND recorded_at <= ?"
                    " GROUP BY event_type",
                    (principal_id, start_ts, end_ts),
                ).fetchall()

            for row in rows:
                by_type[row[0]] = int(row[1])
                total += int(row[1])

        except Exception:  # noqa: BLE001
            logger.exception(
                "analytics: summary query failed for principal=%r", principal_id
            )

        return {
            "principal_id": principal_id,
            "period_days": period_days,
            "period_start": start_date.isoformat(),
            "period_end": reference_date.isoformat(),
            "total_events": total,
            "by_event_type": by_type,
        }
