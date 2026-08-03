# MCP Client Setup — Cursor, Windsurf, and Cline

Connect AI code editors to the DepthFusion MCP server at `https://mcp.tonracein.com`.
All three editors below use the **Streamable HTTP transport** (`POST /mcp`, MCP spec 2025-03-26).

> **Token:** Set `DEPTHFUSION_MCP_TOKEN` in your shell environment before launching your editor.
> Never paste a real token in any config file committed to version control.
> Your token lives in `~/.claude/depthfusion.env` — look for the `DEPTHFUSION_MCP_TOKEN=` line.

---

## Prerequisites

- A DepthFusion MCP token (contact your admin or generate one with `openssl rand -hex 32` for self-hosted).
- Your editor version must support the **MCP 2025-03-26 Streamable HTTP** transport:
  - **Cursor** ≥ 0.43 (Settings → Beta → Enable MCP)
  - **Windsurf** ≥ 1.0 (built-in MCP support)
  - **Cline** ≥ 3.0 (VSCode extension)

---

## Cursor

Cursor stores MCP server definitions in `~/.cursor/mcp.json` (global) or
`.cursor/mcp.json` at the project root (workspace-scoped).

```json
{
  "mcpServers": {
    "depthfusion": {
      "type": "http",
      "url": "https://mcp.tonracein.com/mcp",
      "headers": {
        "Authorization": "Bearer ${DEPTHFUSION_MCP_TOKEN}"
      }
    }
  }
}
```

**Steps:**

1. Open a terminal and confirm the env var is set: `echo $DEPTHFUSION_MCP_TOKEN`
2. Create or edit `~/.cursor/mcp.json` with the snippet above.
3. Restart Cursor (or run **Cursor: Restart MCP Servers** from the command palette).
4. Open the MCP panel (View → MCP) — `depthfusion` should appear as **Connected**.

---

## Windsurf

Windsurf reads MCP configuration from `~/.codeium/windsurf/mcp_config.json`.

```json
{
  "mcpServers": {
    "depthfusion": {
      "serverType": "http",
      "url": "https://mcp.tonracein.com/mcp",
      "headers": {
        "Authorization": "Bearer ${DEPTHFUSION_MCP_TOKEN}"
      }
    }
  }
}
```

**Steps:**

1. Confirm the env var is set in the shell you use to launch Windsurf: `echo $DEPTHFUSION_MCP_TOKEN`
2. Create or edit `~/.codeium/windsurf/mcp_config.json` with the snippet above.
3. Restart Windsurf.
4. Open Cascade → click the plug icon → verify `depthfusion` is listed and active.

---

## Cline (VSCode Extension)

Cline persists MCP settings in VSCode's `settings.json`. Open the Cline settings panel
(**Cline: Open Settings** from the command palette) and add under `cline.mcpServers`:

```json
{
  "cline.mcpServers": {
    "depthfusion": {
      "type": "streamableHttp",
      "url": "https://mcp.tonracein.com/mcp",
      "headers": {
        "Authorization": "Bearer ${DEPTHFUSION_MCP_TOKEN}"
      },
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
```

**Steps:**

1. Confirm the env var is set: `echo $DEPTHFUSION_MCP_TOKEN`
2. In VSCode, open **Settings** (JSON) and add the block above (merge into any existing `cline.mcpServers`).
3. Reload the VSCode window (**Developer: Reload Window**).
4. Open the Cline sidebar → MCP Servers — `depthfusion` should appear as **Connected**.

---

## Verifying the Connection

All three editors issue an `initialize` JSON-RPC call on connect. You can replicate
the same round-trip from the command line to confirm your token works:

```bash
# Replace $DEPTHFUSION_MCP_TOKEN with your token value (or export it first)
curl -s -X POST https://mcp.tonracein.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer ${DEPTHFUSION_MCP_TOKEN}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {"name": "curl-test", "version": "1.0"}
    }
  }'
```

**Expected response (initialize):**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "depthfusion", "version": "2.0.0"}
  }
}
```

**Verify tools/list round-trip:**

```bash
curl -s -X POST https://mcp.tonracein.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer ${DEPTHFUSION_MCP_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

**Expected:** a JSON response listing 31 tools (e.g. `depthfusion_recall_relevant`, `depthfusion_status`, …).

**Smoke-test transcript (live server, 2026-08-03):**

```
$ curl -s -X POST https://mcp.tonracein.com/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer ${DEPTHFUSION_MCP_TOKEN}" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl-test","version":"1.0"}}}'

{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"depthfusion","version":"2.0.0"}}}

$ curl -s -X POST https://mcp.tonracein.com/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer ${DEPTHFUSION_MCP_TOKEN}" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(f'tool_count={len(d[\"result\"][\"tools\"])}')"

tool_count=31
```

If you receive `401 Unauthorized` or `{"detail":{"error":"invalid_token",...}}`, re-check the token.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Token not set or wrong value | `echo $DEPTHFUSION_MCP_TOKEN`; re-export the correct value |
| `Connection refused` | Editor launched before env var was set | Set the env var, then restart the editor |
| `curl: (6) Could not resolve host` | Network/DNS issue | `curl -sf https://mcp.tonracein.com/health` to confirm reachability |
| Tools list empty | Editor using legacy SSE transport | Confirm `type` is `http` / `streamableHttp` (not `sse`) and URL ends in `/mcp` |
| `tool_count=0` in curl test | Using the SSE endpoint by mistake | The SSE endpoint is `/sse`; Streamable HTTP is `/mcp` |

---

## Related docs

- [docs/mcp-http-server.md](mcp-http-server.md) — transport internals and server configuration
- [docs/chatgpt-mcp-setup.md](chatgpt-mcp-setup.md) — ChatGPT Desktop setup (uses SSE transport)
- [docs/cli.md](cli.md) — CLI usage for standalone token operations
