# Coordination Brief: Cloudflare Tunnel — DepthFusion MCP Hostname
**From:** agent-ops PM
**To:** DepthFusion PM
**Date:** 2026-06-01
**Subject:** Extending the shared AMC Cloudflare tunnel to expose DepthFusion HTTP MCP (port 7301)

---

## What Agent-Ops Is Building

The shared Cloudflare tunnel (Zero Trust → Networks → Tunnels → "AMC tunnel") currently exposes the AMC dashboard at `127.0.0.1:8090`. We are extending that same tunnel to give DepthFusion a public hostname so remote team members can connect their local Claude Code directly to the DepthFusion MCP server without an SSH tunnel.

Planned topology (all via the single shared TUNNEL_TOKEN):
```
depthfusion.<domain>  →  cloudflared  →  localhost:7301   (DepthFusion HTTP MCP)
amc.<domain>          →  cloudflared  →  localhost:8090   (AMC dashboard)
<skillforge hostname> →  cloudflared  →  localhost:????   (SkillForge — planned)
<sourcefin hostname>  →  cloudflared  →  localhost:3002   (SourceFin demo — planned)
```

The ingress rule is configured in the Cloudflare Zero Trust dashboard (not a local config.yml — the tunnel runs in token-only mode).

---

## What Agent-Ops Will Configure

Once the domain is confirmed and Greg sets the DNS A-record:

1. **Add public hostname in Cloudflare Zero Trust dashboard:**
   - Tunnel: AMC tunnel
   - Public hostname: `depthfusion.<domain>`
   - Service URL: `http://localhost:7301`
   - No additional CF Access policy unless DepthFusion PM requests one

2. **Verify cloudflared metrics port** is bound to `127.0.0.1:20241` (not `0.0.0.0`) — cloudflared will restart when the new hostname is added, so this must be clean first.

3. **Token rotation prerequisite:** The de-argv fix for `docker-compose.tunnel.yml` landed in commit `aeff6a5a`. Before adding a new hostname, Greg must confirm the Cloudflare dashboard token has been rotated and `cloudflared` has been restarted with the new `TUNNEL_TOKEN` on the VPS. If the old token is still live, this must happen first.

4. **Distribute the team MCP config** (see suggested `claude mcp add` command below) once the tunnel is confirmed live.

---

## Decisions Needed from the DepthFusion PM

Please confirm or clarify each of the following before agent-ops adds the ingress rule.

### A. Target port — 7301 or 7300?

Survey found two relevant ports:
- **Port 7301** — HTTP MCP server (`GET /sse`, `POST /messages`). SSE-based, 2-endpoint MCP protocol. This is what Claude Code's MCP client expects.
- **Port 7300** — REST API (DepthFusion's own HTTP REST, not MCP protocol).

**Action needed:** Confirm the tunnel should target `localhost:7301` (the MCP HTTP server), not 7300. If the MCP HTTP server is not currently running on the VPS, confirm it needs to be started first (see question F below).

### B. Auth — how should the tunnel endpoint be protected?

The survey found that `src/depthfusion/mcp/http_server.py` only enforces the Bearer token when `DEPTHFUSION_MCP_PUBLIC=1` is set **and** `DEPTHFUSION_MCP_TOKEN` is non-empty. When `DEPTHFUSION_MCP_PUBLIC=1` is absent, the server runs with zero auth at the application layer.

Two viable options:

**Option 1 — Loopback bind + CF as the auth layer (simpler)**
- `cloudflared` proxies to `127.0.0.1:7301` without changing the server's bind
- `DEPTHFUSION_MCP_PUBLIC=1` is NOT set — server stays unauthenticated at app layer
- Cloudflare Access policy (JWT or one-time PIN) gates the public hostname
- Pro: no env changes needed. Con: any process on the VPS can call 7301 unauthenticated.

**Option 2 — Public bind + Bearer token auth (defence in depth)**
- Set `DEPTHFUSION_MCP_PUBLIC=1` and `DEPTHFUSION_MCP_TOKEN=<secret>` in the server env
- Server binds `0.0.0.0:7301` and enforces Bearer token on `/sse` and `/messages`
- Agent-ops adds the public hostname; team members include `Authorization: Bearer <token>` in their MCP config
- Pro: auth at both CF and app layers. Con: requires env change + service restart + token distribution.

**Recommendation from agent-ops:** Option 2 (defence in depth). The `cloudflared` proxy to `127.0.0.1` is already sufficient for loopback isolation, but Option 2 means a stolen CF token alone can't reach DepthFusion unauthenticated.

**Action needed:** Which option do you prefer? If Option 2, provide the `DEPTHFUSION_MCP_TOKEN` value (or confirm where to source it) so agent-ops can include it in the team distribution note.

### C. CORS

The HTTP MCP server has no `CORSMiddleware` configured. For CLI-based Claude Code clients (the intended transport), CORS is irrelevant. However, if any team member's setup makes browser-originated requests (web UI wrappers, Anthropic Console MCP testing), CORS will block them.

**Action needed:** Should `CORSMiddleware` be added before the tunnel goes live? If yes, suggest allowed origins (at minimum `https://depthfusion.<domain>`). Agent-ops can prepare the BACKLOG story if DepthFusion PM wants to defer this.

### D. Bind address — 0.0.0.0 or stay on 127.0.0.1?

Cloudflared can proxy to `127.0.0.1:7301` without the server rebinding. This is the safer default — the server stays loopback-only and cloudflared acts as the bridge.

If Option 2 (Bearer token auth) is chosen in §B, the server must bind `0.0.0.0` (`DEPTHFUSION_MCP_PUBLIC=1`). If Option 1, the server stays on `127.0.0.1` and no env change is needed.

**Action needed:** This is answered by the §B choice. Confirming here for explicit sign-off.

### E. Suggested `claude mcp add` command for team members

Once the tunnel is live, team members will run something like:

**Option 1 (no app-layer auth):**
```bash
claude mcp add depthfusion-remote \
  --transport sse \
  --url "https://depthfusion.<domain>/sse"
```

**Option 2 (Bearer token auth):**
```bash
claude mcp add depthfusion-remote \
  --transport sse \
  --url "https://depthfusion.<domain>/sse" \
  --header "Authorization: Bearer <DEPTHFUSION_MCP_TOKEN>"
```

Note: The SSE transport uses session UUIDs in query parameters (`?sessionId=`). Cloudflare must pass these through unchanged on `POST /messages` requests. Standard Cloudflare tunnel passthrough does this correctly with HTTP/1.1 proxying — no special config needed unless HTTP/2 is forced.

**Action needed:** Confirm the intended command template (agent-ops will include it in the team runbook).

### F. Is the HTTP MCP server currently running on the VPS?

`DEPTHFUSION_MCP_HTTP_ENABLED` defaults to `False` in `config.py`. The survey also found that `infra/systemd/README.md` references `depthfusion-mcp.service` as "already present" but the `.service` file does not exist in the repo (`infra/systemd/` only has `depthfusion-rest.service`).

**Action needed:** Confirm whether `depthfusion-mcp.service` is installed and running on the VPS (`systemctl status depthfusion-mcp`), or whether it needs to be created and started before the tunnel is added. If not running, the ingress rule would point to a dead port.

### G. Token distribution mechanism

If Option 2 is chosen, team members need `DEPTHFUSION_MCP_TOKEN` distributed securely. Is there a 1Password vault, Vault server, or other mechanism already in use for DepthFusion secrets, or will this be shared out-of-band?

---

## Open Questions Flagged by Both Surveys

| # | Item | Owner | Urgency |
|---|------|--------|---------|
| OQ-1 | **Lock state** — both prod-host locks in `locks.jsonl` are expired but not formally released (skillforge claim ts:2026-05-31T06:32:37Z, agent-ops reboot claim ts:2026-05-31T15:32:07Z). Append release entries before touching the box. | agent-ops PM | Before any VPS work |
| OQ-2 | **Token rotation** — de-argv fix merged in `aeff6a5a`. Confirm Greg has rotated the CF token and restarted cloudflared before adding new hostnames. | Greg / agent-ops PM | Before adding tunnel hostname |
| OQ-3 | **Domain value** — `<domain>` is a placeholder. Confirm the actual domain pointing at 176.9.147.206. | Greg | Before creating hostname |
| OQ-4 | **cloudflared metrics port** — potentially bound to `0.0.0.0:20241`. Remap to `127.0.0.1:20241` before cloudflared restarts. | agent-ops PM | Before adding hostname |
| OQ-5 | **depthfusion-mcp.service** — service file missing from repo, VPS status unknown. Must be running before tunnel is useful. | DepthFusion PM | Blocking |
| OQ-6 | **HTTP server enabled** — `DEPTHFUSION_MCP_HTTP_ENABLED` defaults to False. VPS env must set it True. | DepthFusion PM | Blocking |
| OQ-7 | **CORS gap** — no CORS policy. Decide whether to add it pre-launch or defer. | DepthFusion PM | Low/deferred |

---

## Proposed Handshake Sequence

1. DepthFusion PM answers §A–§G above
2. DepthFusion PM confirms port 7301 is live: `curl http://127.0.0.1:7301/health`
3. Greg rotates CF tunnel token + confirms domain
4. Agent-ops PM resolves OQ-1 (lock entries) and OQ-4 (metrics port bind)
5. Agent-ops PM adds `depthfusion.<domain> → localhost:7301` in CF Zero Trust dashboard
6. Agent-ops PM verifies: `curl https://depthfusion.<domain>/health`
7. Agent-ops PM publishes team runbook with `claude mcp add` command
8. Team members onboard

---

## Reference

- agent-ops tunnel compose: `/home/gregmorris/projects/agent-ops/infra/docker-compose.tunnel.yml`
- DepthFusion HTTP MCP server: `/home/gregmorris/projects/depthfusion/src/depthfusion/mcp/http_server.py`
- DepthFusion remote access docs: `/home/gregmorris/projects/depthfusion/docs/bi-connectivity.md`
- locks.jsonl: `/home/gregmorris/agent-hub-context/coordination/locks.jsonl`
