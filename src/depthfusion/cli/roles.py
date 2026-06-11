"""Admin CLI for RBAC role management.

T-558: Admin CLI + API for role management with audit events.

Usage::

    depthfusion roles assign <principal_id> <role>
    depthfusion roles revoke <principal_id> <role>
    depthfusion roles list

The DB path is read from the ``DEPTHFUSION_DATA_DIR`` environment variable
(falling back to ``~/.depthfusion``).  An audit event is logged to
``$DEPTHFUSION_DATA_DIR/audit.jsonl`` on every role change.

Audit event format (JSONL)::

    {
        "ts": "<ISO-8601>",
        "action": "role_assigned" | "role_revoked",
        "principal_id": "<target principal>",
        "role": "<role value>",
        "actor": "cli",
        "result": "ok" | "not_found"
    }
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from depthfusion.authz.roles import Role, RoleStore

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "identity.db"


def _default_audit_path() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "audit.jsonl"


def _role_store(db_path: Path | None = None) -> RoleStore:
    return RoleStore(db_path or _default_db_path())


def _log_audit_event(
    action: str,
    principal_id: str,
    role: str,
    result: str,
    audit_path: Path | None = None,
) -> None:
    """Append an audit event to the JSONL audit log.

    Parameters
    ----------
    action:
        ``"role_assigned"`` or ``"role_revoked"``.
    principal_id:
        The principal whose role changed.
    role:
        The role value string.
    result:
        ``"ok"`` or ``"not_found"``.
    audit_path:
        Path to the JSONL audit log.  Defaults to
        ``$DEPTHFUSION_DATA_DIR/audit.jsonl``.
    """
    path = audit_path or _default_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "principal_id": principal_id,
        "role": role,
        "actor": "cli",
        "result": result,
    }

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")

    log.info(
        "audit",
        action=action,
        principal_id=principal_id,
        role=role,
        result=result,
    )


def _parse_role(role_str: str) -> Role | None:
    """Parse a role string to a Role enum; return None on invalid input."""
    try:
        return Role(role_str.lower())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------


def cmd_assign(
    principal_id: str,
    role_str: str,
    db_path: Path | None = None,
    audit_path: Path | None = None,
) -> int:
    """Assign *role_str* to *principal_id*.

    Returns
    -------
    int
        Exit code (0 = success, 2 = invalid role).
    """
    role = _parse_role(role_str)
    if role is None:
        valid = ", ".join(r.value for r in Role)
        print(
            f"Error: invalid role {role_str!r}. Valid roles: {valid}",
            file=sys.stderr,
        )
        return 2

    store = _role_store(db_path)
    store.grant(principal_id, role, granted_by="cli")

    _log_audit_event(
        action="role_assigned",
        principal_id=principal_id,
        role=role.value,
        result="ok",
        audit_path=audit_path,
    )

    print(f"Role '{role.value}' assigned to '{principal_id}'.")
    return 0


def cmd_revoke(
    principal_id: str,
    role_str: str,
    db_path: Path | None = None,
    audit_path: Path | None = None,
) -> int:
    """Revoke *role_str* from *principal_id*.

    Returns
    -------
    int
        Exit code (0 = revoked, 1 = not found, 2 = invalid role).
    """
    role = _parse_role(role_str)
    if role is None:
        valid = ", ".join(r.value for r in Role)
        print(
            f"Error: invalid role {role_str!r}. Valid roles: {valid}",
            file=sys.stderr,
        )
        return 2

    store = _role_store(db_path)
    removed = store.revoke(principal_id, role)

    result = "ok" if removed else "not_found"
    _log_audit_event(
        action="role_revoked",
        principal_id=principal_id,
        role=role.value,
        result=result,
        audit_path=audit_path,
    )

    if removed:
        print(f"Role '{role.value}' revoked from '{principal_id}'.")
        return 0
    else:
        print(
            f"Warning: '{principal_id}' did not have role '{role.value}'.",
            file=sys.stderr,
        )
        return 1


def cmd_list(db_path: Path | None = None) -> int:
    """Print all role assignments to stdout.

    Returns
    -------
    int
        Exit code (0 = success, 1 = no assignments found).
    """
    store = _role_store(db_path)
    assignments = store.list_assignments()

    if not assignments:
        print("No role assignments found.")
        return 1

    header = f"{'PRINCIPAL_ID':<36}  {'ROLE':<10}  {'GRANTED_BY':<20}  GRANTED_AT"
    print(header)
    print("-" * len(header))

    for a in assignments:
        ts = datetime.fromtimestamp(a["granted_at"], tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        print(
            f"{a['principal_id']:<36}  "
            f"{a['role']:<10}  "
            f"{a['granted_by']:<20}  "
            f"{ts}"
        )
    return 0


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the appropriate sub-command.

    Parameters
    ----------
    argv:
        Argument list (excluding the program name).  Defaults to
        :data:`sys.argv[1:]` when *None*.

    Returns
    -------
    int
        Exit code.
    """
    args = list(argv if argv is not None else sys.argv[1:])

    if not args or args[0] in ("-h", "--help"):
        print(
            "Usage:\n"
            "  depthfusion roles assign <principal_id> <role>\n"
            "  depthfusion roles revoke <principal_id> <role>\n"
            "  depthfusion roles list\n"
            "\n"
            f"Valid roles: {', '.join(r.value for r in Role)}\n"
        )
        return 0

    sub = args[0]

    if sub == "assign":
        if len(args) < 3:
            print(
                "Error: 'assign' requires <principal_id> and <role> arguments.",
                file=sys.stderr,
            )
            return 2
        return cmd_assign(args[1], args[2])

    elif sub == "revoke":
        if len(args) < 3:
            print(
                "Error: 'revoke' requires <principal_id> and <role> arguments.",
                file=sys.stderr,
            )
            return 2
        return cmd_revoke(args[1], args[2])

    elif sub == "list":
        return cmd_list()

    else:
        print(f"Error: unknown sub-command {sub!r}.", file=sys.stderr)
        print("Available: assign, revoke, list", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["cmd_assign", "cmd_revoke", "cmd_list", "main"]
