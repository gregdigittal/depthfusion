## Transport scenarios

The DepthFusion MCP layer supports three distinct usage patterns:

### Scenario A — Claude Desktop (stdio)

Claude Desktop spawns `python -m depthfusion.mcp.server` as a child process and communicates over stdin/stdout. No HTTP server is involved. Configuration in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "depthfusion": {
      "command": "python",
      "args": ["-m", "depthfusion.mcp.server"],
      "env": {
        "DEPTHFUSION_API_TOKEN": "<token>"
      }
    }
  }
}
```

The stdio path requires **only the base package** — no HTTP/FastAPI extras needed. `pip install depthfusion` (no extra) is sufficient.

### Scenario B — Claude Code (Streamable HTTP)

Claude Code ≥2.1.x uses the **Streamable HTTP transport** (MCP spec 2025-03-26). The long-running HTTP server must be started first:

```bash
DEPTHFUSION_V2_LEGACY_AUTH=1 \
DEPTHFUSION_API_TOKEN=<token> \
DEPTHFUSION_MCP_HOST=127.0.0.1 \
DEPTHFUSION_MCP_PORT=7301 \
  python -m depthfusion.mcp.http_server
```

Register in `~/.claude/settings.json` (or `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "depthfusion": {
      "type": "http",
      "url": "http://127.0.0.1:7301/mcp",
      "headers": {
        "Authorization": "Bearer ${DEPTHFUSION_API_TOKEN}"
      }
    }
  }
}
```

One server instance can serve multiple Claude Code windows simultaneously — each opens its own session via `initialize`.

### Scenario C — Claude.ai Remote Custom Connector

A Claude.ai Remote Custom Connector requires a **publicly reachable HTTPS endpoint** that
Anthropic's infrastructure can reach.

> **Important:** `127.0.0.1`, `localhost`, Tailscale-only, and private-VPN URLs will
> **NOT** work as cloud-brokered connectors. Anthropic's servers cannot route to private
> addresses. Use a public domain with valid TLS (e.g. `https://mcp.yourserver.com/mcp`).

Set `DEPTHFUSION_MCP_HOST=0.0.0.0` and place a TLS-terminating reverse proxy (nginx, Caddy,
or Cloudflare Tunnel) in front of port 7301. Configure `DEPTHFUSION_MCP_ALLOWED_ORIGINS`
to restrict the `Origin` header when the server is publicly reachable.

For Tailscale or other private-network setups, continue using Scenario B (Claude Code with the
Tailscale IP) — the `/mcp` endpoint is reachable to clients on the same network without
requiring a public URL.

---

## Transports

| Endpoint | Transport | MCP spec | Required by |
|---|---|---|---|
| `POST /mcp` | Streamable HTTP | 2025-03-26 | Claude Code ≥2.1.x |
| `GET /sse` + `POST /messages` | SSE (two-endpoint) | pre-2025-03-26 | Claude Code ≤2.0.x |
| `GET /health` | — | — | unauthenticated probe |

Both transports share the same `require_principal` auth dependency and `_process_request` dispatcher.

### Streamable HTTP endpoint reference (MCP 2025-03-26)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mcp` | JSON-RPC request/response. `initialize` issues `Mcp-Session-Id`. Notifications (no `id`) → `202 Accepted`. |
| `GET` | `/mcp` | Server-initiated `text/event-stream` keyed by `Mcp-Session-Id`. |
| `DELETE` | `/mcp` | Session teardown. Requires `Mcp-Session-Id`. |

**Accept negotiation:** `POST /mcp` honours the `Accept` header. Clients that send only `text/event-stream` receive a single-event SSE response.

**Protocol-version validation:** `MCP-Protocol-Version: 2025-03-26` accepted; anything else → `400`. Omitting the header is tolerated for back-compat.

**Origin validation (DNS-rebinding guard):** `/mcp` routes check the `Origin` header when `DEPTHFUSION_MCP_ALLOWED_ORIGINS` is set. Absent `Origin` is always accepted (CLI tools and Claude Code do not send it). When the env var is **not set**, the check is fully permissive (any `Origin` accepted). When set, only listed origins are accepted; unlisted origins receive `403 {"error":"Origin not allowed"}`. Set `DEPTHFUSION_MCP_ALLOWED_ORIGINS` to a comma-separated list when the server is publicly reachable.

#### Curl examples

```bash
TOKEN=$DEPTHFUSION_API_TOKEN

# initialize — capture Mcp-Session-Id
SESSION=$(curl -s -D - -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0.1"}}}' \
  http://127.0.0.1:7301/mcp | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')

# tools/list using the session
curl -s -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SESSION" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  http://127.0.0.1:7301/mcp | python3 -m json.tool

# notification — returns 202 with empty body
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SESSION" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
  http://127.0.0.1:7301/mcp

# teardown
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SESSION" \
  http://127.0.0.1:7301/mcp
```

---

## Auth and environment variables

All `/mcp`, `/sse`, and `/messages` endpoints require a valid Bearer token. `/health` is unauthenticated.

| Variable | Required | Description |
|---|---|---|
| `DEPTHFUSION_V2_LEGACY_AUTH` | Yes (dev) | Set to `1` to use bearer-token auth backed by `DEPTHFUSION_API_TOKEN` |
| `DEPTHFUSION_API_TOKEN` | Yes (dev) | Shared bearer token for legacy auth mode |
| `DEPTHFUSION_MCP_HOST` | No | Bind address (default `127.0.0.1`) |
| `DEPTHFUSION_MCP_PORT` | No | Bind port (default `7301`) |
| `DEPTHFUSION_MCP_ALLOWED_ORIGINS` | No | Comma-separated allowed `Origin` values for `/mcp` routes. When unset, all origins are accepted (permissive). When set, only listed origins pass; others get `403`. |

If `DEPTHFUSION_V2_LEGACY_AUTH=1` is set without `DEPTHFUSION_API_TOKEN`, startup fails. In production, remove `DEPTHFUSION_V2_LEGACY_AUTH` and configure OIDC/JWKS via the `DEPTHFUSION_OIDC_*` variables.

---

## Health probe

```bash
curl --silent --max-time 2 --fail http://127.0.0.1:7301/health
```

Returns: `{"status": "ok", "transports": ["sse", "streamable-http"], "version": "<ver>"}`.

The `session-start.sh` hook probes this URL before reporting DepthFusion availability. If unreachable, it falls back to the Python subprocess path non-fatally so Claude Code startup is never blocked.

---

## Multi-client sharing

One HTTP server instance serves multiple Claude Code sessions. Each client calls `initialize` on `POST /mcp` to receive a unique `Mcp-Session-Id`, then routes subsequent requests using that header. The server maintains a per-session queue so responses are delivered over the correct stream. This avoids the per-window Python subprocess startup cost.
