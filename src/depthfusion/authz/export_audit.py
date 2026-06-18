"""Export auditing, server-backstop rate limiting & anomaly heuristics — S-193.

This module sits on top of :mod:`depthfusion.audit.log` (the append-only audit
store) and :mod:`depthfusion.authz.export_controls` (the policy matrix). It
provides the server-side enforcement that complements client-side export
controls:

* **Audit (T-667 AC-1)** — every export-class action, *allowed or denied*, is
  written to the audit log carrying principal, record, action, decision and
  device. :func:`audit_export_action` is the single orchestration point.
* **Rate-limit backstop (T-667 AC-2)** — :class:`ExportRateLimiter` enforces a
  per-principal sliding-window cap. The client is expected to self-limit, but
  the server never trusts the client: a burst above the configured threshold is
  rejected here regardless of what the client allowed.
* **Anomaly heuristics (T-668)** — :class:`ExportAnomalyDetector` inspects the
  recent export history of a principal and flags two patterns to an admin
  notification channel:
    - a *burst* (more than ``burst_threshold`` exports in ``window_seconds``);
    - a *cross-project sweep* (exports touching at least
      ``project_sweep_threshold`` distinct projects in the window).

Design notes
------------
- No secrets are read here; the alert channel is an injectable callable so a
  production deployment can wire it to email/Slack/webhook without this module
  importing any transport.
- All time inputs default to :func:`time.time` but are injectable for tests.
- The rate limiter and detector share the audit store as their source of
  truth, so they observe the same history the auditors see — there is no second
  bookkeeping store to drift.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from depthfusion.audit.log import AuditEvent, AuditEventType, AuditStore
from depthfusion.authz.export_controls import (
    ClassificationLevel,
    ExportDecision,
    ExportFormat,
    ExportPolicyMatrix,
    check_export_allowed,
)

__all__ = [
    "AnomalyKind",
    "AnomalyAlert",
    "AlertChannel",
    "ExportRateLimiter",
    "ExportAnomalyDetector",
    "ExportAuditResult",
    "audit_export_action",
]


# ---------------------------------------------------------------------------
# Anomaly model + alert channel
# ---------------------------------------------------------------------------

class AnomalyKind(str, Enum):
    """Categories of export anomaly the heuristics recognise."""

    BURST = "burst"
    CROSS_PROJECT_SWEEP = "cross_project_sweep"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"


@dataclass
class AnomalyAlert:
    """A single anomaly raised against a principal.

    Attributes
    ----------
    kind:
        Which heuristic fired.
    principal_id:
        The actor the anomaly is attributed to.
    detail:
        Human-readable description for the admin notification.
    count:
        The observed count that tripped the threshold (exports in window, or
        distinct projects swept).
    threshold:
        The configured threshold that was exceeded.
    timestamp:
        When the alert was raised (Unix seconds).
    projects:
        Distinct project ids implicated (for cross-project sweeps). Empty for
        burst-only alerts.
    """

    kind: AnomalyKind
    principal_id: str
    detail: str
    count: int
    threshold: int
    timestamp: float = field(default_factory=time.time)
    projects: tuple[str, ...] = ()


# An alert channel is any callable that accepts an AnomalyAlert. Production wires
# this to email/Slack/webhook; tests pass a list-appending stub.
AlertChannel = Callable[[AnomalyAlert], None]


# ---------------------------------------------------------------------------
# Rate-limit backstop (T-667)
# ---------------------------------------------------------------------------

class ExportRateLimiter:
    """Per-principal sliding-window export rate limiter.

    This is a *server backstop*: the client is expected to enforce its own
    limit, but the server independently tracks each principal's recent export
    attempts and rejects any that would push the principal above
    ``max_exports`` within the trailing ``window_seconds``.

    The limiter is thread-safe and keeps an in-memory deque of recent
    timestamps per principal. Entries older than the window are evicted lazily
    on each call, so memory stays bounded by the active export rate.

    Parameters
    ----------
    max_exports:
        Maximum number of *allowed* exports permitted per principal within the
        window. Must be >= 1.
    window_seconds:
        Trailing window length in seconds. Must be > 0.
    """

    def __init__(self, *, max_exports: int = 100, window_seconds: float = 3600.0) -> None:
        if max_exports < 1:
            raise ValueError("max_exports must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max = max_exports
        self._window = float(window_seconds)
        self._lock = threading.RLock()
        self._hits: dict[str, deque[float]] = {}

    @property
    def max_exports(self) -> int:
        return self._max

    @property
    def window_seconds(self) -> float:
        return self._window

    def _evict(self, dq: "deque[float]", now: float) -> None:
        cutoff = now - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()

    def current_count(self, principal_id: str, *, now: Optional[float] = None) -> int:
        """Return the number of in-window exports recorded for *principal_id*."""
        ts = time.time() if now is None else now
        with self._lock:
            dq = self._hits.get(principal_id)
            if dq is None:
                return 0
            self._evict(dq, ts)
            return len(dq)

    def check(self, principal_id: str, *, now: Optional[float] = None) -> bool:
        """Return ``True`` if a new export is within the limit (read-only).

        Does not record the export — use :meth:`record` to commit a successful
        export against the principal's budget.
        """
        return self.current_count(principal_id, now=now) < self._max

    def record(self, principal_id: str, *, now: Optional[float] = None) -> None:
        """Record a successful export against *principal_id*'s budget."""
        ts = time.time() if now is None else now
        with self._lock:
            dq = self._hits.setdefault(principal_id, deque())
            self._evict(dq, ts)
            dq.append(ts)

    def acquire(self, principal_id: str, *, now: Optional[float] = None) -> bool:
        """Atomically check-and-record a single export.

        Returns ``True`` and counts the export if the principal is under the
        limit; returns ``False`` and records nothing if the principal is at or
        above the limit (the backstop rejection).
        """
        ts = time.time() if now is None else now
        with self._lock:
            dq = self._hits.setdefault(principal_id, deque())
            self._evict(dq, ts)
            if len(dq) >= self._max:
                return False
            dq.append(ts)
            return True


# ---------------------------------------------------------------------------
# Anomaly heuristics (T-668)
# ---------------------------------------------------------------------------

class ExportAnomalyDetector:
    """Detects export bursts and cross-project sweeps from audit history.

    The detector reads the principal's recent *allowed* export events directly
    from the :class:`AuditStore`, so it observes exactly what was audited. When
    a heuristic trips it builds an :class:`AnomalyAlert`, emits it to the
    injected ``alert_channel``, and writes an ``ANOMALY_DETECTED`` audit event
    for durable record.

    Parameters
    ----------
    store:
        The audit store to read export history from and to write anomaly events
        to.
    alert_channel:
        Callable invoked with each :class:`AnomalyAlert`. Defaults to a no-op.
    burst_threshold:
        Trigger a BURST alert when the count of in-window exports *exceeds*
        this value.
    project_sweep_threshold:
        Trigger a CROSS_PROJECT_SWEEP alert when the number of distinct
        projects touched within the window is *at least* this value.
    window_seconds:
        Trailing window inspected for both heuristics.
    """

    def __init__(
        self,
        store: AuditStore,
        *,
        alert_channel: Optional[AlertChannel] = None,
        burst_threshold: int = 50,
        project_sweep_threshold: int = 5,
        window_seconds: float = 3600.0,
    ) -> None:
        if burst_threshold < 1:
            raise ValueError("burst_threshold must be >= 1")
        if project_sweep_threshold < 1:
            raise ValueError("project_sweep_threshold must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._store = store
        self._channel: AlertChannel = alert_channel or (lambda _alert: None)
        self._burst_threshold = burst_threshold
        self._sweep_threshold = project_sweep_threshold
        self._window = float(window_seconds)

    def _recent_exports(self, principal_id: str, now: float) -> list[dict]:
        since = now - self._window
        events = self._store.query(
            since=since,
            actor=principal_id,
            event_type=AuditEventType.EXPORT_ALLOWED,
            limit=10_000,
        )
        return events

    def evaluate(
        self, principal_id: str, *, now: Optional[float] = None
    ) -> list[AnomalyAlert]:
        """Inspect *principal_id*'s recent exports and raise any anomalies.

        Returns the list of alerts raised (possibly empty). Each alert is also
        dispatched to the alert channel and persisted as an audit event.
        """
        ts = time.time() if now is None else now
        events = self._recent_exports(principal_id, ts)
        alerts: list[AnomalyAlert] = []

        # Heuristic 1: burst
        count = len(events)
        if count > self._burst_threshold:
            alerts.append(
                AnomalyAlert(
                    kind=AnomalyKind.BURST,
                    principal_id=principal_id,
                    detail=(
                        f"Principal {principal_id!r} performed {count} exports in the "
                        f"last {int(self._window)}s (threshold {self._burst_threshold})."
                    ),
                    count=count,
                    threshold=self._burst_threshold,
                    timestamp=ts,
                )
            )

        # Heuristic 2: cross-project sweep
        projects = {
            ev["project_id"] for ev in events if ev.get("project_id")
        }
        if len(projects) >= self._sweep_threshold:
            ordered = tuple(sorted(projects))
            alerts.append(
                AnomalyAlert(
                    kind=AnomalyKind.CROSS_PROJECT_SWEEP,
                    principal_id=principal_id,
                    detail=(
                        f"Principal {principal_id!r} exported across {len(projects)} "
                        f"distinct projects in the last {int(self._window)}s "
                        f"(threshold {self._sweep_threshold})."
                    ),
                    count=len(projects),
                    threshold=self._sweep_threshold,
                    timestamp=ts,
                    projects=ordered,
                )
            )

        for alert in alerts:
            self._dispatch(alert)
        return alerts

    def raise_rate_limit_alert(
        self,
        principal_id: str,
        *,
        observed: int,
        threshold: int,
        now: Optional[float] = None,
    ) -> AnomalyAlert:
        """Build, dispatch and persist a RATE_LIMIT_EXCEEDED alert."""
        ts = time.time() if now is None else now
        alert = AnomalyAlert(
            kind=AnomalyKind.RATE_LIMIT_EXCEEDED,
            principal_id=principal_id,
            detail=(
                f"Principal {principal_id!r} hit the export rate-limit backstop "
                f"({observed} exports vs limit {threshold})."
            ),
            count=observed,
            threshold=threshold,
            timestamp=ts,
        )
        self._dispatch(alert)
        return alert

    def _dispatch(self, alert: AnomalyAlert) -> None:
        # Persist first (durable), then notify. A failing channel must not lose
        # the durable record.
        try:
            self._store.log(
                AuditEvent(
                    event_type=AuditEventType.ANOMALY_DETECTED,
                    actor_principal_id=alert.principal_id,
                    resource_id=alert.kind.value,
                    classification="",
                    timestamp=alert.timestamp,
                    success=False,
                    project_id=",".join(alert.projects),
                )
            )
        finally:
            self._channel(alert)


# ---------------------------------------------------------------------------
# Orchestration — the single export call-site
# ---------------------------------------------------------------------------

@dataclass
class ExportAuditResult:
    """Outcome of a fully-audited export attempt.

    Attributes
    ----------
    allowed:
        Final decision: ``True`` iff the export may proceed (policy permitted
        AND the rate-limit backstop allowed it).
    decision:
        The underlying :class:`ExportDecision` from the policy matrix.
    rate_limited:
        ``True`` when the policy permitted the export but the server backstop
        rejected it as a burst.
    reason:
        Human-readable explanation of the final decision.
    alerts:
        Any anomaly alerts raised during this evaluation.
    audit_event_id:
        Row id of the primary export audit event written.
    """

    allowed: bool
    decision: ExportDecision
    rate_limited: bool
    reason: str
    alerts: list[AnomalyAlert] = field(default_factory=list)
    audit_event_id: int = -1


def audit_export_action(
    *,
    store: AuditStore,
    principal_id: str,
    resource_id: str,
    classification: ClassificationLevel,
    export_format: ExportFormat,
    device_id: str = "",
    project_id: str = "",
    ip_addr: str = "",
    matrix: Optional[ExportPolicyMatrix] = None,
    approval_token: Optional[str] = None,
    rate_limiter: Optional[ExportRateLimiter] = None,
    anomaly_detector: Optional[ExportAnomalyDetector] = None,
    now: Optional[float] = None,
) -> ExportAuditResult:
    """Evaluate, enforce and audit a single export-class action.

    This is the single server-side enforcement point for exports (S-193 AC-1).
    Every call writes an audit event — whether the export is allowed, denied by
    policy, or rejected by the rate-limit backstop — carrying principal,
    record, action, decision and device.

    Order of operations:

    1. Consult the export policy matrix (:func:`check_export_allowed`).
    2. If policy denies → audit an ``EXPORT_DENIED`` event and return.
    3. If policy allows but the rate-limit backstop rejects → audit an
       ``EXPORT_RATE_LIMITED`` event, raise a rate-limit anomaly, return.
    4. Otherwise → audit an ``EXPORT_ALLOWED`` event, then run anomaly
       heuristics (burst / cross-project sweep) against the freshly-recorded
       history.

    Parameters mirror :func:`check_export_allowed` with the addition of
    ``device_id`` / ``project_id`` (for the audit record and sweep heuristic)
    and the injectable ``rate_limiter`` / ``anomaly_detector``.

    Returns
    -------
    ExportAuditResult
        The final decision plus any anomaly alerts raised.
    """
    ts = time.time() if now is None else now

    decision = check_export_allowed(
        classification,
        export_format,
        matrix=matrix,
        approval_token=approval_token,
    )

    base_kwargs: dict[str, Any] = dict(
        actor_principal_id=principal_id,
        resource_id=resource_id,
        classification=classification.value,
        timestamp=ts,
        ip_addr=ip_addr,
        device_id=device_id,
        project_id=project_id,
    )

    # Step 2: policy denial
    if not decision.allowed:
        event_id = store.log(
            AuditEvent(
                event_type=AuditEventType.EXPORT_DENIED,
                success=False,
                **base_kwargs,
            )
        )
        return ExportAuditResult(
            allowed=False,
            decision=decision,
            rate_limited=False,
            reason=decision.reason,
            audit_event_id=event_id,
        )

    # Step 3: rate-limit backstop
    if rate_limiter is not None and not rate_limiter.acquire(principal_id, now=ts):
        event_id = store.log(
            AuditEvent(
                event_type=AuditEventType.EXPORT_RATE_LIMITED,
                success=False,
                **base_kwargs,
            )
        )
        alerts: list[AnomalyAlert] = []
        if anomaly_detector is not None:
            alerts.append(
                anomaly_detector.raise_rate_limit_alert(
                    principal_id,
                    observed=rate_limiter.current_count(principal_id, now=ts) + 1,
                    threshold=rate_limiter.max_exports,
                    now=ts,
                )
            )
        return ExportAuditResult(
            allowed=False,
            decision=decision,
            rate_limited=True,
            reason=(
                "Export rejected by server rate-limit backstop: principal exceeded "
                f"{rate_limiter.max_exports} exports per "
                f"{int(rate_limiter.window_seconds)}s."
            ),
            alerts=alerts,
            audit_event_id=event_id,
        )

    # Step 4: allowed — audit then run anomaly heuristics
    event_id = store.log(
        AuditEvent(
            event_type=AuditEventType.EXPORT_ALLOWED,
            success=True,
            **base_kwargs,
        )
    )

    raised: list[AnomalyAlert] = []
    if anomaly_detector is not None:
        raised = anomaly_detector.evaluate(principal_id, now=ts)

    return ExportAuditResult(
        allowed=True,
        decision=decision,
        rate_limited=False,
        reason=decision.reason,
        alerts=raised,
        audit_event_id=event_id,
    )
