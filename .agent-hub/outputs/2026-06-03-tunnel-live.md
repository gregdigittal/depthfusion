# DepthFusion CF Tunnel — LIVE
**Date:** 2026-06-03
**Status:** ✅ Production

## Tunnel details

| Item | Value |
|------|-------|
| Public URL | `https://depthfusion.tonracein.com` |
| Tunnel | agent-ops tunnel (not AMC) |
| Backend | `localhost:7301` (depthfusion-mcp.service) |
| Health | `{"status":"ok","transport":"sse","version":"1.0.0"}` |
| Auth | Bearer token (prefix `3cea5648`) |

## MCP connection command (distribute to team)

```bash
claude mcp add depthfusion \
  --transport http \
  https://depthfusion.tonracein.com/sse \
  --header "Authorization: Bearer <DEPTHFUSION_MCP_TOKEN>"
```

Token value: distribute via secure channel.
`DEPTHFUSION_MCP_TOKEN` is in `~/.claude/depthfusion.env` on the VPS.

## OQ resolution history

| OQ | Item | Resolved by |
|----|------|-------------|
| OQ-1 | Lock releases in locks.jsonl | agent-ops |
| OQ-2 | CF token rotation confirmed | agent-ops |
| OQ-3 | Domain: tonracein.com confirmed | agent-ops |
| OQ-4 | cloudflared metrics → 127.0.0.1:20241 | agent-ops (2026-06-03) |
| OQ-5 | depthfusion-mcp.service running | depthfusion PM (2026-06-02) |
| OQ-6 | DEPTHFUSION_MCP_HTTP_ENABLED=true | depthfusion PM (2026-06-02) |
| OQ-7 | CORS deferred (not needed for MCP clients) | depthfusion PM (2026-06-02) |

## Note on tunnel ownership

Earlier coordination docs referred to the "AMC tunnel". The ingress rule was added
to the **agent-ops tunnel**, not AMC. The `2026-06-01-cloudflare-tunnel-coordination.md`
brief is superseded by this document.
