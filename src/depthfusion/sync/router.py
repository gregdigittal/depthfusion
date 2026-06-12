"""DepthFusion V2 — Sync REST endpoints.

E-52 / S-167 / T-584 T-585

Routes
------
POST /v2/sync/push
    Push a delta of records from a device. Only records where the
    calling principal is listed in ``acl_allow`` are accepted.

GET  /v2/sync/pull?since=<token>
    Pull records visible to the calling principal that changed since
    the opaque cursor ``token``.  Returns the new high-water token.

Authentication: all routes require a verified Principal via
``require_principal`` from ``depthfusion.api.auth``.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator

from depthfusion.api.auth import require_principal
from depthfusion.identity.models import Principal
from depthfusion.sync.engine import _VALID_CLASSIFICATIONS, Record, SyncEngine, SyncResult

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v2/sync", tags=["sync"])

# ---------------------------------------------------------------------------
# Engine singleton
# ---------------------------------------------------------------------------

_engine: SyncEngine | None = None


def _get_engine() -> SyncEngine:
    global _engine
    if _engine is None:
        data_dir = os.environ.get("DEPTHFUSION_DATA_DIR", "/tmp")
        _engine = SyncEngine(db_path=str(os.path.join(data_dir, "sync.db")))
    return _engine


def _set_engine(engine: SyncEngine) -> None:
    """Override the engine — used in tests."""
    global _engine
    _engine = engine


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RecordBody(BaseModel):
    """Wire representation of a sync record."""

    record_id: str
    acl_allow: list[str]
    classification: str = "internal"
    payload: dict = {}

    @field_validator("classification")
    @classmethod
    def check_classification(cls, v: str) -> str:
        if v not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"classification must be one of {sorted(_VALID_CLASSIFICATIONS)!r}"
            )
        return v


class PushRequest(BaseModel):
    records: list[RecordBody]


class PushResponse(BaseModel):
    accepted: list[str]
    rejected: dict[str, str]


class PullResponse(BaseModel):
    records: list[dict]
    next_token: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/push", response_model=PushResponse)
async def push_records(
    body: PushRequest,
    principal: Annotated[Principal, Depends(require_principal)],
) -> PushResponse:
    """Push a batch of records from the calling device.

    Only records where the principal is in ``acl_allow`` are accepted.
    Server wins on ``acl_allow`` and ``classification`` if the record
    already exists.
    """
    engine = _get_engine()

    records: list[Record] = [
        Record(
            record_id=rb.record_id,
            principal_id=principal.principal_id,
            acl_allow=rb.acl_allow,
            classification=rb.classification,
            payload=rb.payload,
        )
        for rb in body.records
    ]

    result: SyncResult = engine.sync_push(principal, records)

    log.info(
        "sync.push.complete",
        principal_id=principal.principal_id,
        accepted=len(result.accepted),
        rejected=len(result.rejected),
    )
    return PushResponse(accepted=result.accepted, rejected=result.rejected)


@router.get("/pull", response_model=PullResponse)
async def pull_records(
    principal: Annotated[Principal, Depends(require_principal)],
    since: int = Query(default=0, ge=0, description="Opaque sync cursor (changelog rowid)"),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> PullResponse:
    """Pull records visible to the calling principal.

    Only records where the principal's ID is in ``acl_allow`` are
    returned. Pass ``since=0`` to retrieve all records (initial sync).

    Returns a ``next_token`` cursor the client should store and pass
    as ``since=`` on the next pull.
    """
    engine = _get_engine()

    since_dt: datetime = engine.datetime_for_token(since)
    records = engine.sync_pull(principal, since=since_dt, limit=limit)
    next_token = engine.latest_token()

    log.info(
        "sync.pull.complete",
        principal_id=principal.principal_id,
        count=len(records),
        since_token=since,
        next_token=next_token,
    )
    return PullResponse(
        records=[
            {
                "record_id": r.record_id,
                "principal_id": r.principal_id,
                "acl_allow": r.acl_allow,
                "classification": r.classification,
                "payload": r.payload,
                "updated_at": r.updated_at,
            }
            for r in records
        ],
        next_token=next_token,
    )


__all__ = ["router", "_set_engine"]
