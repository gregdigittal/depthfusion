## Transports

The HTTP MCP server (`src/depthfusion/mcp/http_server.py`) supports two MCP transports:

| Endpoint | Transport | MCP spec | Required by |
|---|---|---|---|
| `GET /sse` + `POST /messages` | SSE (two-endpoint) | pre-2025-03-26 | Claude Code ≤2.0.x |
| `POST /mcp` | Streamable HTTP | MCP 2025-03-26 | Claude Code ≥2.1.x |

`GET /health` reports `"transports": ["sse", "streamable-http"]` and is unauthenticated.

Both transports share the same `require_principal` auth dependency and the same `_process_request` dispatcher.

### Streamable HTTP endpoint reference (MCP 2025-03-26)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mcp` | JSON-RPC request/response. `initialize` issues `Mcp-Session-Id`; requests bearing the header route to that session. Notifications (no `id`) → `202 Accepted`. |
| `GET` | `/mcp` | Server-initiated `text/event-stream` keyed by `Mcp-Session-Id`. |
| `DELETE` | `/mcp` | Session teardown. Requires `Mcp-Session-Id`; removes the session registry entry. |

**Accept negotiation:** `POST /mcp` honours the `Accept` header. Clients that send only `text/event-stream` receive a single-event SSE response instead of JSON.

**Protocol-version validation:** requests carrying `MCP-Protocol-Version` are validated; anything other than `2025-03-26` is rejected with `400`. The header may be omitted for back-compat.

#### Curl examples

```bash
# initialize — note the Mcp-Session-Id in the response headers
TOKEN=$DEPTHFUSION_API_TOKEN
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

# send a notification (no response body expected — returns 202)
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

## Auth requirements

The HTTP MCP server lives in `src/depthfusion/mcp/http_server.py` and exposes the MCP two-endpoint SSE transport on `GET /sse` and `POST /messages`. `/health` is unauthenticated, but `/sse` and `/messages` are protected by FastAPI's `require_principal` dependency from `src/depthfusion/api/auth.py`.

For the current shared-server setup, use the legacy token auth path:

```bash
DEPTHFUSION_V2_LEGACY_AUTH=1
DEPTHFUSION_API_TOKEN=<shared-secret>
DEPTHFUSION_MCP_HOST=127.0.0.1
DEPTHFUSION_MCP_PORT=7301
```

`DEPTHFUSION_V2_LEGACY_AUTH=1` explicitly selects bearer-token auth backed by `DEPTHFUSION_API_TOKEN`. Clients must send `Authorization: Bearer <token>`. If `DEPTHFUSION_V2_LEGACY_AUTH=1` is set without `DEPTHFUSION_API_TOKEN`, startup fails. If neither full OIDC/JWKS auth nor this legacy-token pair is configured, `require_principal` fails closed instead of allowing unauthenticated MCP calls.

The `/sse` endpoint requires `require_principal` before it opens the SSE stream, so even read-only MCP sessions must authenticate. The same requirement applies to `/messages`, which carries JSON-RPC requests for the active SSE session.

## Curl verification

After the server is running and `DEPTHFUSION_API_TOKEN` is exported in the shell, verify the authenticated SSE endpoint with:

```bash
curl --max-time 5 -i -H "Authorization: Bearer $DEPTHFUSION_API_TOKEN" http://127.0.0.1:7301/sse
```

Expected result: HTTP 200 with `content-type: text/event-stream`, followed by an SSE payload beginning with:

```text
event: endpoint
data: /messages?sessionId=<uuid>
```

The command times out after five seconds because the SSE stream is designed to stay open.

## Claude Code MCP registration

Register Claude Code against the HTTP SSE endpoint rather than spawning `python -m depthfusion.mcp.server` for each client. Put this in `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "depthfusion": {
      "type": "sse",
      "url": "http://127.0.0.1:7301/sse",
      "headers": {
        "Authorization": "Bearer ${DEPTHFUSION_API_TOKEN}"
      }
    }
  }
}
```

If the installed Claude Code build reads MCP servers from `~/.claude/settings.json`, place the same `mcpServers.depthfusion` object there instead and preserve the rest of the settings file. Remote clients can use the same registration with the host changed to the private reachable address, such as a Tailscale IP:

```json
"url": "http://100.x.y.z:7301/sse"
```

Each Claude Code process must have `DEPTHFUSION_API_TOKEN` in its environment so the registration can send the bearer token.

## Multi-client sharing

One long-running HTTP MCP server instance can serve multiple Claude Code clients. Each client opens its own `GET /sse` connection, receives a unique `sessionId`, and sends JSON-RPC MCP requests to `POST /messages?sessionId=...`. The server keeps a separate queue per session, so responses are routed back over the matching SSE stream.

This lets many local or Tailscale-connected Claude Code sessions share the same DepthFusion process, loaded code, configuration, caches, and backing stores. It also avoids the startup cost and isolation of one Python MCP subprocess per Claude Code window.

## Python-subprocess fallback path

The deployed Claude `session-start.sh` hook probes the HTTP MCP server before relying on local Python work. It builds the health URL from the MCP host and port:

```bash
DEPTHFUSION_HEALTH_URL="http://${DEPTHFUSION_MCP_HOST:-127.0.0.1}:${DEPTHFUSION_MCP_PORT:-7301}/health"
```

Then it runs a bounded health check:

```bash
curl --silent --max-time 2 --fail "$DEPTHFUSION_HEALTH_URL"
```

If `/health` responds, the hook can report the HTTP MCP server as available in the session-start context. If the HTTP server is unavailable, the hook logs a warning and falls back to the existing Python subprocess path so session startup still gets best-effort DepthFusion context. That fallback runs the DepthFusion virtualenv Python locally, imports the session-start or tagging logic, and exits non-fatally if DepthFusion cannot be reached. Claude Code startup must not be blocked by either the HTTP probe or the subprocess fallback.
