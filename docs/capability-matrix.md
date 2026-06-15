# Capability Matrix — DepthFusion V2 RBAC

> **Source of truth:** `src/depthfusion/authz/roles.py` — `ROLE_CAPABILITIES`.
> This document is generated from that mapping; update the code first.

## Overview

DepthFusion V2 uses a four-tier role hierarchy.  Each role grants an
**exact** set of capabilities — no capability is inferred by possession of
a higher role unless it is explicitly listed in that role's set.

```
viewer  <  member  <  admin  <  owner
```

`owner` holds every capability (it equals `set(Capability)`).
Each lower role holds a strict subset of the role above it.

---

## Role Definitions

| Role | Description |
|------|-------------|
| `owner` | Full access — all capabilities. Intended for the deployment owner. |
| `admin` | Manage users/devices/settings; read all records. Cannot grant `owner` role to others without also holding `assign_roles` (which only `owner` has). |
| `member` | Create and manage own records; read shared records. Default for team members. |
| `viewer` | Read-only access to records shared with them. No write, no admin. |

---

## Capability Matrix

`✓` = granted · `—` = not granted

| Capability | viewer | member | admin | owner |
|-----------|:------:|:------:|:-----:|:-----:|
| `read_own_records` | ✓ | ✓ | ✓ | ✓ |
| `read_shared_records` | ✓ | ✓ | ✓ | ✓ |
| `create_own_records` | — | ✓ | ✓ | ✓ |
| `write_own_records` | — | ✓ | ✓ | ✓ |
| `read_all_records` | — | — | ✓ | ✓ |
| `read_restricted` | — | — | — | ✓ |
| `write_all_records` | — | — | — | ✓ |
| `manage_users` | — | — | ✓ | ✓ |
| `manage_devices` | — | — | ✓ | ✓ |
| `manage_settings` | — | — | ✓ | ✓ |
| `view_audit_log` | — | — | ✓ | ✓ |
| `assign_roles` | — | — | — | ✓ |
| `revoke_roles` | — | — | — | ✓ |

---

## Capability Descriptions

### Record Operations

| Capability | Description |
|------------|-------------|
| `create_own_records` | Create records owned by the principal. |
| `read_own_records` | Read records owned by the principal. |
| `read_shared_records` | Read records shared with the principal (in `acl_allow` but not owner). |
| `read_all_records` | Read any record regardless of `acl_allow` (admin/owner override). |
| `read_restricted` | Read records with `classification=restricted` (elevated privilege). |
| `write_own_records` | Update/delete records owned by the principal. |
| `write_all_records` | Update/delete any record (admin/owner override). |

### User and Device Management

| Capability | Description |
|------------|-------------|
| `manage_users` | Create, update, delete principal accounts. |
| `manage_devices` | Register, revoke, and inspect device leases. |

### Settings and Configuration

| Capability | Description |
|------------|-------------|
| `manage_settings` | Change system-wide and project-level configuration. |

### Audit and Observability

| Capability | Description |
|------------|-------------|
| `view_audit_log` | Read the audit/event log across all principals. |

### Role Administration

| Capability | Description |
|------------|-------------|
| `assign_roles` | Grant or revoke roles on behalf of other principals. Owner-only. |
| `revoke_roles` | Revoke role assignments (subset of `assign_roles` for least-privilege). Owner-only. |

---

## Privilege Escalation Properties

The role hierarchy enforces strict subset inclusion:

```
viewer_caps ⊂ member_caps ⊂ admin_caps ⊂ owner_caps
```

This is enforced by `tests/test_role_matrix.py` (parameterized per-role
exact-match tests) and `tests/test_roles.py` (subset/superset assertions).

---

## Admin CLI Usage

```bash
# Assign a role
depthfusion roles assign <principal_id> <role>

# Revoke a role
depthfusion roles revoke <principal_id> <role>

# List all assignments
depthfusion roles list
```

Audit events are written to `$DEPTHFUSION_DATA_DIR/audit.jsonl` on every
role change.

---

## REST API

### `POST /v2/admin/roles`

Assign or revoke a role.  Requires `assign_roles` capability (owner only).

```json
{
  "principal_id": "user-abc",
  "role": "member",
  "action": "assign"
}
```

Response:
```json
{
  "principal_id": "user-abc",
  "role": "member",
  "action": "assign",
  "result": "ok"
}
```

### `GET /v2/admin/roles`

List all role assignments.  Requires `view_audit_log` capability (admin+).

---

## Extension Guide

To add a new capability:

1. Add the value to `Capability` in `src/depthfusion/authz/roles.py`.
2. Add it to the appropriate role(s) in `ROLE_CAPABILITIES`.
3. Update this document.
4. The parameterized tests in `tests/test_role_matrix.py` will catch any
   matrix divergence automatically on the next test run.
