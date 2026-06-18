# ADR-S191 — MCP HTTP Auth Env Var Rename (DEPTHFUSION_MCP_TOKEN → DEPTHFUSION_V2_LEGACY_AUTH + DEPTHFUSION_API_TOKEN)

**Decision ID:** ADR-S191  
**Ticket:** S-191 / T-701  
**Status:** ACCEPTED  
**Date:** 2026-06-17  
**Author:** Sonnet dev agent (T-701)

---

## Context

The S-191 auth overhaul introduced OIDC/JWKS-backed JWT validation as the primary authentication
path for the DepthFusion MCP HTTP server. As part of that work, the static Bearer token fallback
was redesigned to be explicit opt-in rather than implicit default.

**Before S-191:**
- `DEPTHFUSION_MCP_TOKEN` — static Bearer token accepted by `mcp/http_server.py` when OIDC vars absent.
  The server silently accepted this token with no flag to enable/disable it.

**After S-191:**
- The auth dependency (`api/auth.py`) follows a three-tier priority:
  1. Full OIDC: `DEPTHFUSION_JWKS_URI` + `DEPTHFUSION_OIDC_ISSUER` + `DEPTHFUSION_OIDC_AUDIENCE`
  2. Legacy token: `DEPTHFUSION_V2_LEGACY_AUTH=1` + `DEPTHFUSION_API_TOKEN`
  3. Unconfigured sentinel: always returns HTTP 503 with `auth_not_configured`

The old `DEPTHFUSION_MCP_TOKEN` var is **no longer read**. Any VPS environment file still using
`DEPTHFUSION_MCP_TOKEN` must be migrated or the server will return 503 on all authenticated
endpoints.

---

## The Fix

Update the VPS environment (typically `/etc/systemd/system/depthfusion-mcp.service` or an
`EnvironmentFile`) to remove the old var and add the two new ones:

```
# Remove:
DEPTHFUSION_MCP_TOKEN=<secret>

# Add:
DEPTHFUSION_V2_LEGACY_AUTH=1
DEPTHFUSION_API_TOKEN=<same-secret>
```

After updating the environment file, reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart depthfusion-mcp
```

---

## Code Verification (static analysis, 2026-06-17)

The following files were inspected to confirm the auth fix is present in the codebase:

### `src/depthfusion/api/auth.py`

- `_build_principal_dep()` (line 104) reads `DEPTHFUSION_V2_LEGACY_AUTH` (line 128) and
  `DEPTHFUSION_API_TOKEN` (line 129).
- When `DEPTHFUSION_V2_LEGACY_AUTH=1`, a `_LegacyTokenDep` instance is returned that performs
  constant-time Bearer token comparison.
- When neither OIDC nor legacy auth is configured, `_UnconfiguredPrincipalDep` is returned,
  which always raises HTTP 503 with `auth_not_configured` — fail-closed by design.
- `DEPTHFUSION_MCP_TOKEN` is **not read anywhere** in this file or in `mcp/http_server.py`
  (legacy `_check_mcp_auth` reads it at line 145 of `http_server.py` only as an old fallback
  that is now bypassed by the `require_principal` dependency).

### `src/depthfusion/identity/legacy_shim.py`

- `_ENV_ENABLE = "DEPTHFUSION_V2_LEGACY_AUTH"` (line 47)
- `_ENV_TOKEN = "DEPTHFUSION_API_TOKEN"` (line 48)
- `LegacyTokenShim.from_env()` reads both vars and uses constant-time HMAC comparison
  (`hmac.compare_digest` over SHA-256 digests).
- Every successful legacy authentication logs a `warning`-level deprecation notice so
  operators can track and migrate consumers.

### `/sse` endpoint authentication flow

The `/sse` endpoint in `mcp/http_server.py` (line 174) uses `require_principal` as a
FastAPI `Depends`, not the deprecated `_check_mcp_auth`. The `require_principal` dependency
is the module-level singleton built by `_build_principal_dep()` at import time.

Expected behaviour after fix applied on VPS:

| Request | Expected response |
|---------|------------------|
| `GET /health` | `200 {"status":"ok","transport":"sse","version":"..."}` (unauthenticated) |
| `GET /sse` — no header | `401 Unauthorized` |
| `GET /sse` — wrong token | `401 {"error":"invalid_token",...}` |
| `GET /sse` — `Authorization: Bearer $DEPTHFUSION_API_TOKEN` | `200` with `event: endpoint` SSE |

---

## Production Verification Protocol (T-701)

SSH to VPS was not available from the workflow agent at the time T-701 ran. The following
verification steps are to be executed by an operator when SSH is available:

```bash
# Step 1 — Confirm env vars are set
sudo systemctl show depthfusion-mcp --property=Environment | grep -E "V2_LEGACY|API_TOKEN"

# Step 2 — Restart service
sudo systemctl restart depthfusion-mcp

# Step 3 — Health check (unauthenticated)
curl --max-time 5 http://127.0.0.1:7301/health
# Expected: {"status":"ok","transport":"sse","version":"..."}

# Step 4 — SSE auth check (authenticated)
curl --max-time 5 -H "Authorization: Bearer $DEPTHFUSION_API_TOKEN" \
     http://127.0.0.1:7301/sse
# Expected: HTTP 200, first SSE line: "event: endpoint"
# Must appear within 3 seconds

# Step 5 — Confirm rejection without token
curl --max-time 5 http://127.0.0.1:7301/sse
# Expected: HTTP 401
```

If SSH remains unavailable when this ADR is read, the static code analysis above is the
verification record. The auth logic has been confirmed present at the correct code paths.

---

## Status

- [x] Auth fix present in codebase (`api/auth.py`, `identity/legacy_shim.py`)
- [x] `_LegacyTokenDep` reads `DEPTHFUSION_V2_LEGACY_AUTH` + `DEPTHFUSION_API_TOKEN`
- [x] Fail-closed: unconfigured server returns 503, not open access
- [x] ADR recorded
- [ ] Live VPS smoke test — requires operator with SSH access to complete Step 1–5 above

**T-701 verdict:** Code fix confirmed in source; live VPS curl verification deferred pending
SSH access. Mark complete per task instructions (SSH unavailable → note in ADR, do not block loop).
