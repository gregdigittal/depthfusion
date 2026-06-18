# MCP Auth Verification — T-701

**Date:** 2026-06-17  
**Story:** S-207  
**Task:** T-701  
**Author:** Workflow agent (Sonnet)

---

## Summary

This runbook documents the static code verification of the S-191 auth fix for the DepthFusion
MCP HTTP server. The fix renames `DEPTHFUSION_MCP_TOKEN` to the two-var pattern
`DEPTHFUSION_V2_LEGACY_AUTH=1` + `DEPTHFUSION_API_TOKEN`.

SSH to VPS (176.9.147.206) was not available from the workflow agent. Static analysis was
performed instead. The full ADR is at `docs/decisions/ADR-S191-auth-env-rename.md`.

---

## Files Verified

| File | Finding |
|------|---------|
| `src/depthfusion/api/auth.py` | `_build_principal_dep()` reads `DEPTHFUSION_V2_LEGACY_AUTH` + `DEPTHFUSION_API_TOKEN`; `_LegacyTokenDep` performs constant-time Bearer comparison |
| `src/depthfusion/identity/legacy_shim.py` | `LegacyTokenShim.from_env()` reads same two vars; uses `hmac.compare_digest` on SHA-256 digests |
| `src/depthfusion/mcp/http_server.py` | `/sse` and `/messages` endpoints use `require_principal` (FastAPI Depends); `/health` is unauthenticated |

---

## Auth Priority Chain (confirmed in code)

```
_build_principal_dep() [api/auth.py:104]
  └── DEPTHFUSION_JWKS_URI + OIDC_ISSUER + OIDC_AUDIENCE set?
        → Full OIDC JWT validation (RS256)
      DEPTHFUSION_V2_LEGACY_AUTH=1?
        → _LegacyTokenDep(DEPTHFUSION_API_TOKEN)
      else
        → _UnconfiguredPrincipalDep → HTTP 503 auth_not_configured (fail-closed)
```

---

## Expected Responses After VPS Restart

| Endpoint | Auth | Expected HTTP | Expected Body |
|----------|------|--------------|---------------|
| `GET /health` | None | 200 | `{"status":"ok","transport":"sse","version":"..."}` |
| `GET /sse` | None | 401 | `{"detail":"Not authenticated"}` |
| `GET /sse` | `Bearer $DEPTHFUSION_API_TOKEN` | 200 SSE | `event: endpoint` within 3s |
| `POST /messages` | `Bearer $DEPTHFUSION_API_TOKEN` | 404/200 | depends on sessionId |

---

## Operator Steps (run when SSH is available)

```bash
ssh gregmorris@176.9.147.206

# Confirm env is set
sudo systemctl show depthfusion-mcp --property=Environment | \
  grep -E "V2_LEGACY|API_TOKEN"

# Restart
sudo systemctl restart depthfusion-mcp
sleep 2

# Unauthenticated health check
curl --max-time 5 http://127.0.0.1:7301/health

# Authenticated SSE (returns first SSE event "event: endpoint")
curl --max-time 5 \
  -H "Authorization: Bearer $DEPTHFUSION_API_TOKEN" \
  http://127.0.0.1:7301/sse

# Confirm rejection without token
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:7301/sse
# Expected: 401
```

---

## Verification Status

- [x] `DEPTHFUSION_V2_LEGACY_AUTH` env var read confirmed in `api/auth.py` (line 128)
- [x] `DEPTHFUSION_API_TOKEN` env var read confirmed in `api/auth.py` (line 129)
- [x] `_LegacyTokenDep.__call__` performs constant-time comparison (no timing oracle)
- [x] `/health` endpoint is unauthenticated (line 168-170, `http_server.py`)
- [x] `/sse` uses `require_principal` dependency — not the old `_check_mcp_auth` function
- [x] Fail-closed: no OIDC + no legacy auth → HTTP 503 (not open access)
- [ ] Live VPS curl test — deferred, SSH unavailable during T-701 run

**Result:** Auth fix confirmed present in codebase. Live smoke test to be completed by
operator when SSH is available. Per task instructions, SSH unavailability does not block
the loop — see ADR-S191-auth-env-rename.md.
