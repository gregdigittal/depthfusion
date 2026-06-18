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
