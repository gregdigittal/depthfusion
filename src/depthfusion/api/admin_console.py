"""DepthFusion V2 — Admin Console REST API.

Routes
------
GET /v2/admin/audit
    Query the append-only audit log.
    Params: ``since=<iso>``, ``actor=<id>``, ``event_type=<type>``
    Requires: ``Capability.VIEW_AUDIT_LOG``
    Side-effect: the audit-read action is itself audited.

GET /v2/admin/health
    Returns db_size, record_counts, last_sync, active_devices.
    Requires: ``Capability.VIEW_AUDIT_LOG``

GET /v2/admin/devices
    Lists all registered devices from the device registry.
    Requires: ``Capability.MANAGE_DEVICES``

GET /metrics
    Prometheus-format metrics (request_count, error_rate, search_latency).
    No auth required (metrics are typically scraped by infra tooling).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from depthfusion.api.auth import require_principal
from depthfusion.audit.log import AuditEvent, AuditEventType, AuditStore
from depthfusion.authz import get_policy_engine
from depthfusion.authz.roles import Capability
from depthfusion.identity.device_registry import DeviceRecord, DeviceRegistry
from depthfusion.identity.models import Principal

log = structlog.get_logger(__name__)

router = APIRouter(tags=["admin-console"])


# ---------------------------------------------------------------------------
# Prometheus-style metrics state (in-memory; reset on restart)
# ---------------------------------------------------------------------------

class _MetricsState:
    """Mutable singleton for lightweight in-process metrics."""

    def __init__(self) -> None:
        self.request_count: int = 0
        self.error_count: int = 0
        # search latency samples (seconds)
        self._latency_samples: list[float] = []

    def record_request(self, *, error: bool = False) -> None:
        self.request_count += 1
        if error:
            self.error_count += 1

    def record_search_latency(self, seconds: float) -> None:
        self._latency_samples.append(seconds)
        # keep last 1000 samples
        if len(self._latency_samples) > 1000:
            self._latency_samples = self._latency_samples[-1000:]

    @property
    def search_latency_p50(self) -> float:
        if not self._latency_samples:
            return 0.0
        s = sorted(self._latency_samples)
        return s[len(s) // 2]

    @property
    def search_latency_p95(self) -> float:
        if not self._latency_samples:
            return 0.0
        s = sorted(self._latency_samples)
        return s[int(len(s) * 0.95)]

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count


# Module-level singleton — accessible from middleware / other modules.
metrics_state = _MetricsState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_audit_db() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "audit.db"


def _default_identity_db() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "identity.db"


def _enforce(principal: Principal, capability: Capability) -> None:
    """Raise 403 if *principal* is denied *capability* by the PolicyEngine.

    System-level resources (audit log, devices) carry no per-record ACL, so we
    use a self-admitted sentinel: ``acl_allow=[principal_id]``.  The decision
    cache makes repeat calls cheap.
    """
    decision = get_policy_engine().decide(
        principal,
        capability,
        {"acl_allow": [principal.principal_id]},
    )
    if not decision.allow:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "detail": decision.reason},
        )


def _get_audit_store() -> AuditStore:
    return AuditStore(db_path=_default_audit_db())


def _get_device_registry() -> DeviceRegistry:
    return DeviceRegistry(db_path=_default_identity_db())


def _db_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _record_counts(identity_db: Path) -> dict[str, int]:
    """Return row counts for key tables in the identity database."""
    counts: dict[str, int] = {}
    if not identity_db.exists():
        return counts
    tables = ["principals", "devices", "role_assignments", "audit_events"]
    try:
        with closing(sqlite3.connect(str(identity_db))) as conn:
            for table in tables:
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
                    counts[table] = int(row[0])
                except sqlite3.OperationalError:
                    counts[table] = 0
    except sqlite3.Error:
        pass
    return counts


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DeviceResponse(BaseModel):
    device_id: str
    owner_principal_id: str
    platform: str
    last_sync: float
    revoked: bool


class HealthResponse(BaseModel):
    db_size_bytes: int
    record_counts: dict[str, int]
    last_sync: Optional[float]
    active_devices: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/v2/admin/audit")
async def query_audit_log(
    since: Optional[str] = Query(default=None, description="ISO-8601 datetime"),
    actor: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    principal: Principal = Depends(require_principal),
) -> list[dict]:
    """Return audit events matching the query parameters.

    Requires ``VIEW_AUDIT_LOG`` capability.  The read itself is audited.
    """
    _enforce(principal, Capability.VIEW_AUDIT_LOG)

    since_ts: Optional[float] = None
    if since is not None:
        try:
            dt = datetime.fromisoformat(since)
            since_ts = dt.timestamp()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "invalid_param", "detail": f"'since' must be ISO-8601: {exc}"},
            ) from exc

    store = _get_audit_store()

    # Audit the audit read itself.
    store.log(
        AuditEvent(
            event_type=AuditEventType.RECORD_READ,
            actor_principal_id=principal.principal_id,
            resource_id="audit_log",
            success=True,
        )
    )

    results = store.query(since=since_ts, actor=actor, event_type=event_type)
    log.info(
        "admin.audit_query",
        actor=principal.principal_id,
        result_count=len(results),
        since=since,
        filter_actor=actor,
        filter_event_type=event_type,
    )
    return results


@router.get("/v2/admin/health", response_model=HealthResponse)
async def admin_health(
    principal: Principal = Depends(require_principal),
) -> HealthResponse:
    """Return database size, record counts, last sync, and active device count.

    Requires ``VIEW_AUDIT_LOG`` capability.
    """
    _enforce(principal, Capability.VIEW_AUDIT_LOG)

    identity_db = _default_identity_db()
    audit_db = _default_audit_db()

    # Combine sizes
    total_size = _db_size_bytes(identity_db) + _db_size_bytes(audit_db)
    counts = _record_counts(identity_db)

    # Active (non-revoked) device count
    active_devices = 0
    registry = _get_device_registry()
    try:
        all_devices = registry.list_all()
        active_devices = sum(1 for d in all_devices if not d.revoked)
        last_sync: Optional[float] = (
            max((d.last_sync for d in all_devices), default=None)
            if all_devices else None
        )
    except Exception:
        last_sync = None

    log.info("admin.health_check", actor=principal.principal_id)
    return HealthResponse(
        db_size_bytes=total_size,
        record_counts=counts,
        last_sync=last_sync,
        active_devices=active_devices,
    )


@router.get("/v2/admin/devices", response_model=list[DeviceResponse])
async def list_devices(
    principal: Principal = Depends(require_principal),
) -> list[DeviceResponse]:
    """Return all registered devices (active and revoked).

    Requires ``MANAGE_DEVICES`` capability.
    """
    _enforce(principal, Capability.MANAGE_DEVICES)

    registry = _get_device_registry()
    devices: list[DeviceRecord] = registry.list_all()

    log.info("admin.list_devices", actor=principal.principal_id, count=len(devices))
    return [
        DeviceResponse(
            device_id=d.device_id,
            owner_principal_id=d.owner_principal_id,
            platform=d.platform,
            last_sync=d.last_sync,
            revoked=d.revoked,
        )
        for d in devices
    ]


@router.get("/metrics")
async def prometheus_metrics():  # type: ignore[return]
    """Prometheus-format metrics endpoint.

    Returns
    -------
    str
        Plain-text Prometheus exposition format.
    """
    from fastapi.responses import PlainTextResponse

    lines = [
        "# HELP depthfusion_request_count Total HTTP requests processed",
        "# TYPE depthfusion_request_count counter",
        f"depthfusion_request_count {metrics_state.request_count}",
        "",
        "# HELP depthfusion_error_rate Fraction of requests that resulted in errors",
        "# TYPE depthfusion_error_rate gauge",
        f"depthfusion_error_rate {metrics_state.error_rate:.6f}",
        "",
        "# HELP depthfusion_search_latency_p50_seconds Search latency P50 in seconds",
        "# TYPE depthfusion_search_latency_p50_seconds gauge",
        f"depthfusion_search_latency_p50_seconds {metrics_state.search_latency_p50:.6f}",
        "",
        "# HELP depthfusion_search_latency_p95_seconds Search latency P95 in seconds",
        "# TYPE depthfusion_search_latency_p95_seconds gauge",
        f"depthfusion_search_latency_p95_seconds {metrics_state.search_latency_p95:.6f}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


__all__ = ["router", "metrics_state"]
