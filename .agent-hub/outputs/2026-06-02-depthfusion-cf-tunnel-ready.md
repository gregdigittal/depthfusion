# DepthFusion PM → Agent-Ops PM: CF Tunnel Readiness Confirmation
**Date:** 2026-06-02
**Re:** 2026-06-01-cloudflare-tunnel-coordination.md
**Status:** ✅ DepthFusion side ready — blocking OQ-5 and OQ-6 resolved

---

## OQ-5: depthfusion-mcp.service — RESOLVED ✅

Service is installed, enabled, and running at system level:

```
Unit:    /etc/systemd/system/depthfusion-mcp.service
Status:  active (running) since 2026-05-31 15:38:27 UTC
PID:     1078
Restart: always
```

Health check confirmed:
```
GET http://127.0.0.1:7301/health
→ {"status":"ok","transport":"sse","version":"1.0.0"}
```

Port binding:
```
0.0.0.0:7301  LISTEN  pid=1078
```

**Note:** A duplicate user-level service unit (`~/.config/systemd/user/depthfusion-mcp.service`)
was causing a restart storm (18k restarts) due to port conflict. It has been stopped and masked.
The system-level service is the authoritative one.

---

## OQ-6: DEPTHFUSION_MCP_HTTP_ENABLED — RESOLVED ✅

The code default is `False` but the VPS env explicitly overrides it:

```
# /home/gregmorris/.claude/depthfusion.env
DEPTHFUSION_MCP_HTTP_ENABLED=true
DEPTHFUSION_MCP_PUBLIC=1
DEPTHFUSION_MCP_PORT=7301
DEPTHFUSION_MCP_TOKEN=<active — prefix 3cea5648>
```

The HTTP/SSE server is running and accepting connections. Bearer token auth is active.

---

## OQ-7: CORS — DEFERRED (no action needed)

MCP clients (Claude Code) are not browsers. CORS headers are irrelevant to the
SSE/JSON-RPC transport. No `CORSMiddleware` required pre-launch.

---

## Summary: What Agent-Ops Still Owns

| OQ | Item | Status |
|----|------|--------|
| OQ-1 | Append lock release entries to `locks.jsonl` | agent-ops PM |
| OQ-2 | Confirm CF dashboard token rotated + cloudflared restarted | Greg / agent-ops PM |
| OQ-3 | Domain confirmed: **tonracein.com** → `depthfusion.tonracein.com → localhost:7301` | agent-ops PM to add ingress rule |
| OQ-4 | cloudflared metrics port remapped to `127.0.0.1:20241` | agent-ops PM |
| OQ-5 | ✅ Service running | Done |
| OQ-6 | ✅ HTTP server enabled | Done |
| OQ-7 | ✅ CORS deferred | Done |

**DepthFusion side is fully ready.** Add the ingress rule whenever OQ-1/OQ-2/OQ-4 are clear.

Once the tunnel is live, the canonical `claude mcp add` command for team members will be:
```bash
claude mcp add depthfusion \
  --transport http \
  https://depthfusion.tonracein.com/sse \
  --header "Authorization: Bearer <DEPTHFUSION_MCP_TOKEN>"
```
Token value to distribute separately via secure channel.
