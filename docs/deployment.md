# Deployment — multi-worker constraints and the Redis upgrade path

This document covers two related deployment constraints introduced by S-251
(E-73): the MCP session dict's single-worker limitation, and per-principal
rate limiting on `POST /mcp`.

## Single-worker session dict constraint (S-251 AC-3)

`mcp/http_server.py` keeps two module-level, in-process dicts that track
live MCP sessions:

- `_MCP_SESSIONS` — legacy SSE transport (`GET /sse` + `POST /messages`),
  keyed by `sessionId`.
- `_MCP_STREAMABLE_SESSIONS` — Streamable HTTP transport (`POST/GET/DELETE
  /mcp`), keyed by the `Mcp-Session-Id` header issued on `initialize`.

Both are plain Python `dict[str, asyncio.Queue]` objects living in the
memory of a single worker process. This is correct and sufficient for the
project's default deployment profile — a **single uvicorn worker** bound to
loopback (`127.0.0.1`), serving one operator's Claude Code / Claude Desktop
sessions.

**It is not safe to run `mcp/http_server.py` behind more than one worker
process** (`uvicorn --workers N` with `N > 1`, multiple gunicorn workers, or
any horizontally-scaled deployment) without first replacing these dicts with
a shared, cross-process session store:

- A session opened via `initialize` on worker A registers its
  `Mcp-Session-Id` only in worker A's `_MCP_STREAMABLE_SESSIONS`.
- A subsequent request for that same session, load-balanced to worker B,
  finds no matching entry and receives `404 Session not found or expired` —
  even though the session is legitimately open on worker A.
- The same failure mode applies to the legacy `_MCP_SESSIONS` / `sessionId`
  pairing used by `GET /sse` + `POST /messages`.

### Redis upgrade path for multi-worker session storage

To run more than one worker, replace the in-process dicts with a
Redis-backed session store (Redis Streams / hashes, keyed by session id,
with a TTL matching the session's idle timeout) so every worker resolves
the same session state regardless of which worker accepted the original
`initialize` call. This mirrors the existing `StreamBackend` Protocol
pattern already used elsewhere in the project (`core/event_store.py`'s
`InMemoryStreamBackend` / `RedisStreamBackend`, and this story's own
`mcp/ratelimit.py::RateLimiter` Protocol): define a small `SessionStore`
Protocol with an `InMemorySessionStore` (current dict-based behaviour,
default) and a `RedisSessionStore` (shared across workers) implementation,
selected the same way `DEPTHFUSION_REDIS_URL` already selects
`RedisStreamBackend` in `mcp/tools/_state.py::_get_fabric_store` and
`RedisRateLimitBackend` in `mcp/ratelimit.py::get_rate_limiter`. This
migration is **not** implemented as part of S-251 — it is scoped here as
the documented upgrade path for a future story once multi-worker deployment
is actually required.

**Until that migration lands: run `mcp/http_server.py` with exactly one
worker process.** This is the default (`uvicorn depthfusion.mcp.http_server:app`
with no `--workers` flag starts a single worker) and is the only supported
configuration today.

## Per-principal rate limiting (S-251 AC-1 / AC-2 / AC-4)

`POST /mcp` tool calls are rate-limited per authenticated principal (see
`mcp/ratelimit.py`):

| Env var | Default | Meaning |
|---|---|---|
| `DEPTHFUSION_RATE_LIMIT_PUBLISH` | `60` | Write/admin/audit tool calls allowed per principal per minute |
| `DEPTHFUSION_RATE_LIMIT_RECALL` | `300` | Read-only (recall) tool calls allowed per principal per minute |
| `DEPTHFUSION_REDIS_URL` | unset | When set, rate limiting is backed by Redis (shared across workers); when unset, an in-process counter is used (per-worker only) |

A request over quota receives `HTTP 429` with body:

```json
{"error": "rate_limited", "retry_after_seconds": 37}
```

Like the session dict above, the **default `InMemoryRateLimitBackend` is
per-worker** — with `N` workers and `DEPTHFUSION_REDIS_URL` unset, the
effective limit becomes `limit * N`, not `limit`. Set `DEPTHFUSION_REDIS_URL`
to get a limit that holds correctly across every worker before running more
than one.

## Summary

| Component | Single-worker default | Multi-worker requirement |
|---|---|---|
| MCP session tracking (`_MCP_SESSIONS`, `_MCP_STREAMABLE_SESSIONS`) | In-process dict (works today) | Not yet implemented — documented upgrade path above; single worker only until it lands |
| Rate limiting (`mcp/ratelimit.py`) | `InMemoryRateLimitBackend` (per-worker) | `RedisRateLimitBackend` via `DEPTHFUSION_REDIS_URL` — implemented and safe for multi-worker today |

## Swap Alert

The Aug 6 2026 outage was caused by swap exhaustion from zombie stdio processes.
Monitor swap usage and alert when swap free falls below 20% of total:

```bash
# Check current swap usage
free -h

# One-liner alert threshold check (use in cron or monitoring)
python3 -c "
import subprocess, sys
out = subprocess.check_output(['free', '-b']).decode()
for line in out.splitlines():
    if line.startswith('Swap:'):
        total, used, free = int(line.split()[1]), int(line.split()[2]), int(line.split()[3])
        if total > 0 and free / total < 0.2:
            print(f'ALERT: Swap free {free//1024//1024}MB ({100*free//total}%) < 20%')
            sys.exit(1)
"
```

If using Prometheus + node_exporter, add an alert rule:

```yaml
- alert: SwapLow
  expr: node_memory_SwapFree_bytes / node_memory_SwapTotal_bytes < 0.2
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Host swap below 20% — check for zombie depthfusion stdio processes"
```

## Auth Environment Variables (S-268)

All five auth variables live under the `DEPTHFUSION_*` prefix and are set in
`/home/gregmorris/.claude/depthfusion.env` on VPS deployments.

| Variable | Required when | Description |
|---|---|---|
| `DEPTHFUSION_V2_LEGACY_AUTH` | Always | Set `1` to accept a shared Bearer token (legacy mode). Set `0` for OIDC-only. |
| `DEPTHFUSION_API_TOKEN` | `V2_LEGACY_AUTH=1` | The shared Bearer secret for the REST API (`/api/*`). Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEPTHFUSION_MCP_TOKEN` | `MCP_PUBLIC=1` | Bearer secret for the MCP HTTP server. Same generation command. |
| `DEPTHFUSION_JWKS_URI` | OIDC mode | JWKS endpoint of your IdP, e.g. `https://accounts.google.com/.well-known/openid-configuration` |
| `DEPTHFUSION_OIDC_ISSUER` | OIDC mode | Expected `iss` claim, e.g. `https://accounts.google.com` |
| `DEPTHFUSION_MCP_ALLOWED_ORIGINS` | `MCP_PUBLIC=1` | Comma-separated allowed CORS Origins. Default: `http://localhost,http://127.0.0.1`. Empty string = allow all (dev only). |

See `.env.example` for the full list of configurable variables.
