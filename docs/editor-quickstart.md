# DepthFusion MCP — Editor Quick-Start

Connect Cursor, Windsurf, or Cline to DepthFusion in under two minutes.

Two transport options:

| | **stdio** (local) | **HTTP** (remote / VPS) |
|---|---|---|
| When to use | DepthFusion running on this machine | DepthFusion on a remote VPS |
| Auth | None (process isolation) | Bearer token (`DEPTHFUSION_MCP_TOKEN`) |
| Protocol | MCP stdio JSON-RPC | Streamable HTTP (`POST /mcp`) |

---

## stdio (local)

The wrapper script `scripts/mcp-server.sh` handles the `exec` so Claude CLI's
parser doesn't intercept `-m`.

**Register once:**
```bash
claude mcp add depthfusion -s user \
  /home/gregmorris/projects/depthfusion/scripts/mcp-server.sh
```

### Cursor (`~/.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "depthfusion": {
      "command": "/home/gregmorris/projects/depthfusion/scripts/mcp-server.sh",
      "args": []
    }
  }
}
```

### Windsurf (`~/.codeium/windsurf/mcp_config.json`)
```json
{
  "mcpServers": {
    "depthfusion": {
      "command": "/home/gregmorris/projects/depthfusion/scripts/mcp-server.sh",
      "args": []
    }
  }
}
```

### Cline (VS Code extension settings)
In Cline's MCP settings UI, add a new server:
- **Transport:** stdio
- **Command:** `/home/gregmorris/projects/depthfusion/scripts/mcp-server.sh`
- **Args:** _(empty)_

---

## HTTP (remote VPS)

Requires `DEPTHFUSION_MCP_PUBLIC=1` and `DEPTHFUSION_MCP_TOKEN` set on the server.
Default port: `7301`. Endpoint: `POST https://<host>:7301/mcp`.

### Cursor (`~/.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "depthfusion": {
      "url": "https://mcp.tonracein.com/mcp",
      "headers": {
        "Authorization": "Bearer <your-DEPTHFUSION_MCP_TOKEN>"
      }
    }
  }
}
```

### Windsurf (`~/.codeium/windsurf/mcp_config.json`)
```json
{
  "mcpServers": {
    "depthfusion": {
      "serverUrl": "https://mcp.tonracein.com/mcp",
      "headers": {
        "Authorization": "Bearer <your-DEPTHFUSION_MCP_TOKEN>"
      }
    }
  }
}
```

### Cline (VS Code extension settings)
In Cline's MCP settings UI:
- **Transport:** Streamable HTTP
- **URL:** `https://mcp.tonracein.com/mcp`
- **Headers:** `Authorization: Bearer <your-DEPTHFUSION_MCP_TOKEN>`

---

## Verify connection

After adding the server, ask your editor's AI assistant:

```
List available DepthFusion tools
```

You should see all 32 tools (or the subset for your mode). If you see zero tools,
check that the server is running (`systemctl status depthfusion-mcp`) and that the
token matches.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `import failed` error on stdio start | Run `pip install -e '.[vps-cpu]'` in the project venv |
| 401 on HTTP transport | Check `DEPTHFUSION_MCP_TOKEN` matches the `Authorization` header |
| 403 on HTTP transport | Check `DEPTHFUSION_MCP_ALLOWED_ORIGINS` includes your editor's origin |
| Connection timeout | Confirm `DEPTHFUSION_MCP_PUBLIC=1` and port 7301 is reachable |
| Zero tools listed | Server started but MCP session init failed — check server logs: `journalctl -u depthfusion-mcp -n 50` |
