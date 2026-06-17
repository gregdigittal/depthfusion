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

POST /v2/admin/retention/enforce
    Apply the configured retention policy: purge audit events older than the
    retention window. The enforcement action is itself audited.
    Requires: ``Capability.MANAGE_SETTINGS``

GET /v2/admin/export
    Return the audit/compliance dataset for offline compliance review.
    The export action is itself audited.
    Requires: ``Capability.MANAGE_SETTINGS``

GET /v2/admin/policy
PUT /v2/admin/policy
    Read / replace the export-policy matrix (per-classification export rules).
    Requires: ``Capability.MANAGE_SETTINGS``

GET /v2/admin/classification
PUT /v2/admin/classification
    Read / replace the classification handling-rules table.
    Requires: ``Capability.MANAGE_SETTINGS``

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
from pydantic import BaseModel, Field

from depthfusion.api.auth import require_principal
from depthfusion.audit.log import AuditEvent, AuditEventType, AuditStore
from depthfusion.authz import get_policy_engine
from depthfusion.authz.classification import (
    CLASSIFICATION_POLICY,
    ClassificationLevel,
    HandlingRules,
)
from depthfusion.authz.classification import Role as ClassificationRole
from depthfusion.authz.export_controls import (
    DEFAULT_POLICY_MATRIX,
    ExportFormat,
    ExportPolicy,
    ExportPolicyMatrix,
    check_export_allowed,
)
from depthfusion.authz.export_controls import (
    ClassificationLevel as ExportClassificationLevel,
)
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
# Editable policy stores (in-process; seeded from canonical defaults)
# ---------------------------------------------------------------------------

# Default retention window (days) for compliance audit retention. Configurable
# via the ``DEPTHFUSION_AUDIT_RETENTION_DAYS`` env var; defaults to 365.
_DEFAULT_RETENTION_DAYS = 365


def _retention_days() -> int:
    """Return the configured audit retention window in days."""
    raw = os.environ.get("DEPTHFUSION_AUDIT_RETENTION_DAYS")
    if not raw:
        return _DEFAULT_RETENTION_DAYS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RETENTION_DAYS
    return value if value > 0 else _DEFAULT_RETENTION_DAYS


class _PolicyState:
    """Mutable, admin-editable copy of the export + classification policies.

    Seeded from the canonical module defaults at import time. The PUT routes
    replace entries here; the GET routes read them. This is in-process state
    (reset on restart) — the canonical frozen defaults remain authoritative
    fallbacks.
    """

    def __init__(self) -> None:
        # ``DEFAULT_POLICY_MATRIX`` is keyed by the export_controls
        # ClassificationLevel enum; re-key by the canonical classification
        # ClassificationLevel (identical string values) so the stored map is
        # type-consistent with the editor models.
        self.export_policy: dict[ClassificationLevel, ExportPolicy] = {
            ClassificationLevel(level.value): policy
            for level, policy in DEFAULT_POLICY_MATRIX.items()
        }
        self.classification_rules: dict[ClassificationLevel, HandlingRules] = {
            level: dict(rules)  # type: ignore[misc]
            for level, rules in CLASSIFICATION_POLICY.items()
        }


# Module-level singleton — admin edits land here.
policy_state = _PolicyState()


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


# ── Retention / compliance export (T-671) ──────────────────────────────────

class RetentionEnforceBody(BaseModel):
    """Request body for POST /v2/admin/retention/enforce.

    ``retention_days`` overrides the configured default for this run; when
    omitted the ``DEPTHFUSION_AUDIT_RETENTION_DAYS`` env value (or the
    built-in default) is used.
    """

    retention_days: Optional[int] = Field(default=None, gt=0)


class RetentionEnforceResponse(BaseModel):
    retention_days: int
    cutoff_timestamp: float
    events_purged: int
    events_remaining: int


class ComplianceExportResponse(BaseModel):
    exported_at: float
    record_count: int
    retention_days: int
    classification: ExportClassificationLevel
    export_format: ExportFormat
    watermark_required: bool
    events: list[dict]


# ── Policy + classification editors (T-675) ─────────────────────────────────

class ExportPolicyEntry(BaseModel):
    """A single per-classification export policy (editor payload)."""

    allowed_export_formats: list[ExportFormat] = Field(default_factory=list)
    watermark_required: bool = False
    approval_required: bool = False


class ExportPolicyEditBody(BaseModel):
    """Request body for PUT /v2/admin/policy.

    Maps each ``ClassificationLevel`` (by string value) to its export policy.
    """

    policies: dict[ClassificationLevel, ExportPolicyEntry]


class ExportPolicyResponse(BaseModel):
    policies: dict[ClassificationLevel, ExportPolicyEntry]


class ClassificationRuleEntry(BaseModel):
    """A single per-level classification handling rule (editor payload)."""

    export_allowed: bool = False
    cache_allowed: bool = False
    redact_in_search: bool = True
    allowed_roles: list[ClassificationRole] = Field(default_factory=list)


class ClassificationEditBody(BaseModel):
    """Request body for PUT /v2/admin/classification."""

    rules: dict[ClassificationLevel, ClassificationRuleEntry]


class ClassificationResponse(BaseModel):
    rules: dict[ClassificationLevel, ClassificationRuleEntry]


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


# ── T-671: Retention enforcement + compliance export ───────────────────────

@router.post("/v2/admin/retention/enforce", response_model=RetentionEnforceResponse)
async def enforce_retention(
    body: RetentionEnforceBody,
    principal: Principal = Depends(require_principal),
) -> RetentionEnforceResponse:
    """Apply the configured audit retention policy.

    Purges audit events older than the retention window (default 365 days,
    or ``retention_days`` from the request body). The enforcement action is
    itself written to the audit log.

    Requires ``MANAGE_SETTINGS`` capability.
    """
    _enforce(principal, Capability.MANAGE_SETTINGS)

    days = body.retention_days if body.retention_days is not None else _retention_days()
    cutoff = datetime.now().timestamp() - (days * 86400)

    store = _get_audit_store()
    purged = store.purge_before(cutoff)

    # The enforcement action is itself an audited admin action.
    store.log(
        AuditEvent(
            event_type=AuditEventType.ADMIN_ACTION,
            actor_principal_id=principal.principal_id,
            resource_id="audit_retention",
            success=True,
        )
    )

    remaining = store.count()
    log.info(
        "admin.retention_enforce",
        actor=principal.principal_id,
        retention_days=days,
        events_purged=purged,
        events_remaining=remaining,
    )
    return RetentionEnforceResponse(
        retention_days=days,
        cutoff_timestamp=cutoff,
        events_purged=purged,
        events_remaining=remaining,
    )


def _live_export_matrix() -> ExportPolicyMatrix:
    """Build an ``ExportPolicyMatrix`` from the admin-editable ``policy_state``.

    The export-controls enforcement function keys on its own
    ``ClassificationLevel`` enum; ``policy_state.export_policy`` is keyed by the
    canonical classification ``ClassificationLevel`` (identical string values),
    so re-key into the export_controls enum here.
    """
    return ExportPolicyMatrix(
        policies={
            ExportClassificationLevel(level.value): policy
            for level, policy in policy_state.export_policy.items()
        }
    )


@router.get("/v2/admin/export", response_model=ComplianceExportResponse)
async def compliance_export(
    since: Optional[str] = Query(default=None, description="ISO-8601 datetime"),
    classification: ExportClassificationLevel = Query(
        default=ExportClassificationLevel.INTERNAL,
        description="Classification ceiling of the data being exported.",
    ),
    export_format: ExportFormat = Query(
        default=ExportFormat.JSON,
        description="Requested export format.",
    ),
    approval_token: Optional[str] = Query(
        default=None,
        description="Approval token for classified exports (E-59 T-662).",
    ),
    principal: Principal = Depends(require_principal),
) -> ComplianceExportResponse:
    """Return the audit/compliance dataset for offline review.

    The export action is itself audited.  Before returning any data the
    classification ceiling is checked against the export-controls policy
    (``check_export_allowed``); classified data is not exported without
    approval. A denied decision yields a 403 and is recorded as a failed
    ``EXPORT_STARTED`` audit event.

    Requires ``MANAGE_SETTINGS`` capability.
    """
    _enforce(principal, Capability.MANAGE_SETTINGS)

    since_ts: Optional[float] = None
    if since is not None:
        try:
            since_ts = datetime.fromisoformat(since).timestamp()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "invalid_param", "detail": f"'since' must be ISO-8601: {exc}"},
            ) from exc

    store = _get_audit_store()

    # Respect the export-controls classification ceiling BEFORE returning data.
    decision = check_export_allowed(
        classification,
        export_format,
        matrix=_live_export_matrix(),
        approval_token=approval_token,
    )
    if not decision.allowed:
        # Record the denied export attempt (failed admin export).
        store.log(
            AuditEvent(
                event_type=AuditEventType.EXPORT_STARTED,
                actor_principal_id=principal.principal_id,
                resource_id="compliance_export",
                classification=classification.value,
                success=False,
            )
        )
        log.warning(
            "admin.compliance_export.denied",
            actor=principal.principal_id,
            classification=classification.value,
            export_format=export_format.value,
            reason=decision.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "export_forbidden",
                "detail": decision.reason,
                "approval_required": decision.approval_required,
            },
        )

    events = store.query(since=since_ts, limit=10_000)

    # The export action is itself an audited admin action.
    store.log(
        AuditEvent(
            event_type=AuditEventType.EXPORT_STARTED,
            actor_principal_id=principal.principal_id,
            resource_id="compliance_export",
            classification=classification.value,
            success=True,
        )
    )

    log.info(
        "admin.compliance_export",
        actor=principal.principal_id,
        record_count=len(events),
        classification=classification.value,
        export_format=export_format.value,
    )
    return ComplianceExportResponse(
        exported_at=datetime.now().timestamp(),
        record_count=len(events),
        retention_days=_retention_days(),
        classification=classification,
        export_format=export_format,
        watermark_required=decision.watermark_required,
        events=events,
    )


# ── T-675: Policy + classification editors ──────────────────────────────────

def _export_policy_to_entry(policy: ExportPolicy) -> ExportPolicyEntry:
    return ExportPolicyEntry(
        allowed_export_formats=list(policy.allowed_export_formats),
        watermark_required=policy.watermark_required,
        approval_required=policy.approval_required,
    )


@router.get("/v2/admin/policy", response_model=ExportPolicyResponse)
async def get_export_policy(
    principal: Principal = Depends(require_principal),
) -> ExportPolicyResponse:
    """Return the current export-policy matrix.

    Requires ``MANAGE_SETTINGS`` capability.
    """
    _enforce(principal, Capability.MANAGE_SETTINGS)

    log.info("admin.get_export_policy", actor=principal.principal_id)
    return ExportPolicyResponse(
        policies={
            level: _export_policy_to_entry(policy)
            for level, policy in policy_state.export_policy.items()
        }
    )


@router.put("/v2/admin/policy", response_model=ExportPolicyResponse)
async def put_export_policy(
    body: ExportPolicyEditBody,
    principal: Principal = Depends(require_principal),
) -> ExportPolicyResponse:
    """Replace the export-policy matrix.

    Requires ``MANAGE_SETTINGS`` capability. The change is audited.
    """
    _enforce(principal, Capability.MANAGE_SETTINGS)

    policy_state.export_policy = {
        level: ExportPolicy(
            allowed_export_formats=list(entry.allowed_export_formats),
            watermark_required=entry.watermark_required,
            approval_required=entry.approval_required,
        )
        for level, entry in body.policies.items()
    }

    _get_audit_store().log(
        AuditEvent(
            event_type=AuditEventType.ADMIN_ACTION,
            actor_principal_id=principal.principal_id,
            resource_id="export_policy",
            success=True,
        )
    )
    log.info("admin.put_export_policy", actor=principal.principal_id)
    return ExportPolicyResponse(
        policies={
            level: _export_policy_to_entry(policy)
            for level, policy in policy_state.export_policy.items()
        }
    )


def _rules_to_entry(rules: HandlingRules) -> ClassificationRuleEntry:
    return ClassificationRuleEntry(
        export_allowed=rules["export_allowed"],
        cache_allowed=rules["cache_allowed"],
        redact_in_search=rules["redact_in_search"],
        allowed_roles=list(rules["allowed_roles"]),
    )


@router.get("/v2/admin/classification", response_model=ClassificationResponse)
async def get_classification(
    principal: Principal = Depends(require_principal),
) -> ClassificationResponse:
    """Return the classification handling-rules table.

    Requires ``MANAGE_SETTINGS`` capability.
    """
    _enforce(principal, Capability.MANAGE_SETTINGS)

    log.info("admin.get_classification", actor=principal.principal_id)
    return ClassificationResponse(
        rules={
            level: _rules_to_entry(rules)
            for level, rules in policy_state.classification_rules.items()
        }
    )


@router.put("/v2/admin/classification", response_model=ClassificationResponse)
async def put_classification(
    body: ClassificationEditBody,
    principal: Principal = Depends(require_principal),
) -> ClassificationResponse:
    """Replace the classification handling-rules table.

    Requires ``MANAGE_SETTINGS`` capability. The change is audited.
    """
    _enforce(principal, Capability.MANAGE_SETTINGS)

    policy_state.classification_rules = {
        level: HandlingRules(
            export_allowed=entry.export_allowed,
            cache_allowed=entry.cache_allowed,
            redact_in_search=entry.redact_in_search,
            allowed_roles=list(entry.allowed_roles),
        )
        for level, entry in body.rules.items()
    }

    _get_audit_store().log(
        AuditEvent(
            event_type=AuditEventType.ADMIN_ACTION,
            actor_principal_id=principal.principal_id,
            resource_id="classification_policy",
            success=True,
        )
    )
    log.info("admin.put_classification", actor=principal.principal_id)
    return ClassificationResponse(
        rules={
            level: _rules_to_entry(rules)
            for level, rules in policy_state.classification_rules.items()
        }
    )


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


__all__ = ["router", "metrics_state", "policy_state"]
