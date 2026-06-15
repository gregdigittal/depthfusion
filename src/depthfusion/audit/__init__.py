"""DepthFusion V2 — Audit subsystem.

Provides a tamper-evident, append-only audit log backed by SQLite.
Every security-relevant event (sign-in, sign-out, record access,
record modification, role changes, device enrollment/revocation) is
written here.

The audit_events table is append-only: no DELETE, no UPDATE, ever.
"""
from .log import AuditEvent, AuditEventType, AuditStore

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditStore",
]
