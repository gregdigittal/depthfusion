# DepthFusion Status Report + Heartbeat System
**Date:** 2026-06-03T15:27:08Z  
**Prepared for:** agent-ops  
**Overall Health:** DEGRADED

---

## Service Health Table

| Service | Port | Bind | Status | Uptime | Notes |
|---------|------|------|--------|--------|-------|
| MCP (depthfusion-mcp) | 7301 | **0.0.0.0** | ✅ ACTIVE | ~2d 23h (since May 31) | System-level unit; SSE endpoint publicly exposed |
| REST (depthfusion-rest) | 7300 | 127.0.0.1 | ✅ ACTIVE | ~2d 11h (since Jun 01) | User-level unit; loopback-only — correct |
| cloudflared metrics | 20241 | **\*** | ⚠️ OPEN | N/A — OQ-4 watcher active | No owning process confirmed; 279 checks, still open |

**MCP health endpoint confirmed:** `{"status":"ok","transport":"sse","version":"1.0.0"}`

---

## Open Issues

### CRITICAL — Network Exposure

**Issue 1: MCP port 7301 bound to 0.0.0.0 (all interfaces)**

The MCP SSE endpoint is publicly reachable. REST port 7300 is correctly loopback-only.
This is the same class of exposure that triggered the 2026-04-28 BSI/CERT-Bund advisory.

**Required fix:**
1. Edit the depthfusion-mcp systemd unit (`/etc/systemd/system/depthfusion-mcp.service` or equivalent)
2. Add `--host 127.0.0.1` (or equivalent bind arg) to the server launch command
3. `sudo systemctl daemon-reload && sudo systemctl restart depthfusion-mcp`
4. Verify: `ss -tlnp | grep 7301` should show `127.0.0.1:7301`, not `0.0.0.0:7301`

If public MCP access is intentional (e.g. Cloudflare Tunnel terminates at this port), add an explicit comment to the unit file and ensure the tunnel provides auth + TLS. Even then, binding loopback and having cloudflared proxy inbound is the safer pattern.

---

**Issue 2: OQ-4 — cloudflared metrics port 20241 still bound to \* (all interfaces)**

Status: **STILL OPEN after 279 checks (~25.5 hours of monitoring, since ~2026-06-02 14:00 UTC)**

The OQ-4 watcher has been running continuously:
- `*:20241` present on every single check — no movement
- No owning process visible in `ss` output (`users=` field absent) — may be kernel-level or not visible to current user

**Required action for agent-ops:**

1. Open `docker-compose.tunnel.yml` (cloudflared config)
2. Find the `--metrics` flag in the cloudflared service command
3. Change `--metrics 0.0.0.0:20241` (or `--metrics :20241`) to `--metrics 127.0.0.1:20241`
4. Force-recreate the container:
   ```bash
   docker compose -f docker-compose.tunnel.yml up -d --force-recreate cloudflared
   ```
5. Verify: `ss -tlnp | grep 20241` should show `127.0.0.1:20241` or nothing

Once this is done, the OQ-4 watcher will detect the change and can be stopped.

---

### HIGH — Systemd Unit Misconfiguration

**Issue 3: Malformed user-level MCP unit shadows the working system-level unit**

The file `~/.config/systemd/user/depthfusion-mcp.service` contains an invalid key:
```
Unknown key name StartLimitIntervalSec in section Service
```
This fires a warning in the user journal on every session start. The **system-level** `depthfusion-mcp` service is what is actually serving traffic — it is healthy. But the malformed user-unit causes noise and could cause confusion.

**Recommended fix:**
```bash
# Option A: Remove the stale user-level unit
rm ~/.config/systemd/user/depthfusion-mcp.service
systemctl --user daemon-reload

# Option B: Fix the key (move StartLimitIntervalSec to [Unit] section)
# StartLimitIntervalSec belongs in [Unit], not [Service]
```

---

**Issue 4: Mixed service tiers (system vs user)**

- `depthfusion-mcp` is a **system** service — starts at boot, independent of user session
- `depthfusion-rest` is a **user** service — requires user lingering session

**Action required:** Verify `loginctl enable-linger gregmorris` is set so REST survives user session logout:
```bash
loginctl show-user gregmorris | grep Linger
# Expected: Linger=yes
```
If `Linger=no`, run: `sudo loginctl enable-linger gregmorris`

---

### MEDIUM — Haiku Reranking Disabled

**Issue 5: REST service logs Haiku backend warnings on startup**

```
WARNING: anthropic package not installed — install depthfusion[vps-cpu] to enable Haiku reranking
```
Fires 3 times per restart. Non-fatal — reranking falls back to another strategy — but adds noise to logs.

**Optional fix:** `pip install depthfusion[vps-cpu]` in the venv, or suppress the warning if Haiku reranking is intentionally disabled for this deployment.

---

## Heartbeat System

### What was set up

A 15-minute heartbeat timer was installed on the DepthFusion VPS to automatically publish service health snapshots to `agent-hub-context`.

| Component | Location |
|-----------|----------|
| Script | `/home/gregmorris/projects/depthfusion/.agent-hub/scripts/heartbeat.sh` |
| Systemd timer | `depthfusion-heartbeat.timer` |
| Output files | `agent-hub-context/sessions/depthfusion/heartbeat-YYYY-MM-DDTHH-MM-SS.md` |

### Timer details

- **Interval:** Every 15 minutes
- **Timer installed:** Yes
- **Next scheduled run:** 2026-06-03T15:43:14 UTC (at time of health check)
- **Operational status:** Not yet confirmed — first automated run pending

### How to read heartbeat output

Each heartbeat writes a markdown file to `agent-hub-context/sessions/depthfusion/` with the naming pattern `heartbeat-YYYY-MM-DDTHH-MM-SS.md`. The file contains:
- Timestamp
- `ss -tlnp` snapshot (port bindings)
- systemctl status for both services
- Any detected anomalies

To read the latest heartbeat from agent-ops:
```bash
ls -lt /home/gregmorris/agent-hub-context/sessions/depthfusion/heartbeat-*.md | head -1
# Then read that file
```

Or from the context repo on any machine after `git pull`:
```bash
cd /path/to/agent-hub-context
git pull
ls sessions/depthfusion/heartbeat-*.md | tail -3
```

### Verifying the timer is running

```bash
systemctl --user list-timers | grep heartbeat
# or
systemctl --user status depthfusion-heartbeat.timer
```

---

## What Agent-Ops Needs To Do

Priority order:

| # | Action | Severity | Command/File |
|---|--------|----------|--------------|
| 1 | Rebind cloudflared metrics to loopback | CRITICAL | Edit `docker-compose.tunnel.yml` → `--metrics 127.0.0.1:20241`, then `docker compose up -d --force-recreate cloudflared` |
| 2 | Rebind MCP port 7301 to loopback | CRITICAL | Edit depthfusion-mcp system unit, add `--host 127.0.0.1`, restart service |
| 3 | Verify `loginctl enable-linger` | HIGH | `loginctl show-user gregmorris \| grep Linger` |
| 4 | Remove or fix malformed user-level MCP unit | MEDIUM | `rm ~/.config/systemd/user/depthfusion-mcp.service && systemctl --user daemon-reload` |
| 5 | Confirm heartbeat timer is firing | MEDIUM | Check `agent-hub-context/sessions/depthfusion/` for new `heartbeat-*.md` files after 15:43 UTC |
| 6 | Optionally install Haiku reranking deps | LOW | `pip install depthfusion[vps-cpu]` |

---

## OQ-4 Watcher Summary

The OQ-4 port watcher has been running since approximately 2026-06-02 14:00 UTC.

- **Total checks completed:** ~279
- **Duration monitored:** ~25.5 hours
- **Result on every check:** `*:20241` — no change, no closure
- **Conclusion:** The port will not close on its own. The cloudflared container must be explicitly reconfigured and force-recreated.

Once agent-ops applies the `--metrics 127.0.0.1:20241` fix, the watcher will detect the change. At that point the OQ-4 watcher process can be stopped.
