# systemd Hardening — depthfusion-mcp.service (T-702)

**Date:** 2026-06-17  
**Story:** S-207  
**Task:** T-702  
**Author:** Workflow agent (Sonnet)

---

## Purpose

Add restart rate-limiting guards to `depthfusion-mcp.service` on the VPS to prevent
systemd from entering a restart loop if the MCP server exits repeatedly in a short window.

Without `StartLimitBurst` + `StartLimitIntervalSec`, a misconfigured service (e.g., bad env
vars causing immediate exit) will restart indefinitely, consuming resources and masking the
root cause.

---

## Unit File Changes

Add the following two directives to the `[Unit]` section of
`/etc/systemd/system/depthfusion-mcp.service`:

```ini
[Unit]
Description=DepthFusion MCP HTTP/SSE Server
After=network.target
StartLimitBurst=5
StartLimitIntervalSec=30
```

The full hardened unit file should resemble:

```ini
[Unit]
Description=DepthFusion MCP HTTP/SSE Server
After=network.target
StartLimitBurst=5
StartLimitIntervalSec=30

[Service]
Type=simple
WorkingDirectory=/home/gregmorris/projects/depthfusion
EnvironmentFile=/home/gregmorris/.claude/depthfusion.env
ExecStart=/home/gregmorris/projects/depthfusion/.venv/bin/python3.12 \
    -m uvicorn depthfusion.mcp.http_server:app \
    --host 127.0.0.1 \
    --port 7301 \
    --log-level warning
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

**Explanation of limits:**
- `StartLimitBurst=5`: allow at most 5 restarts in the interval window before entering
  `failed` state.
- `StartLimitIntervalSec=30`: the interval window is 30 seconds.
- Combined effect: if the service crashes 5 times within 30 seconds, systemd stops restarting
  it and marks the unit `failed`. An operator must then `systemctl reset-failed depthfusion-mcp`
  and investigate before restarting.

---

## Operator Commands

Run on VPS (176.9.147.206) as root or with sudo:

```bash
# Step 1 — Edit the unit file
sudo systemctl edit --full depthfusion-mcp.service
# Add StartLimitBurst=5 and StartLimitIntervalSec=30 to [Unit] section

# Step 2 — Reload systemd unit definitions
sudo systemctl daemon-reload

# Step 3 — Restart the service
sudo systemctl restart depthfusion-mcp

# Step 4 — Confirm service is active
sudo systemctl status depthfusion-mcp
# Expected: Active: active (running)

# Step 5 — Verify limits applied
sudo systemctl show depthfusion-mcp | grep -E "StartLimit"
# Expected:
#   StartLimitBurst=5
#   StartLimitIntervalSec=30
```

---

## Recovery If Service Enters Failed State

If the service hits the rate limit (5 crashes in 30s), systemd marks it `failed`:

```bash
# Check status
sudo systemctl status depthfusion-mcp
# Shows: Active: failed

# Reset the failure counter (after diagnosing root cause)
sudo systemctl reset-failed depthfusion-mcp

# Then restart
sudo systemctl start depthfusion-mcp
```

Common causes of rapid restart cycles:
- `DEPTHFUSION_API_TOKEN` not set when `DEPTHFUSION_V2_LEGACY_AUTH=1`
  → server exits at import time with `ValueError`
- Port 7301 already in use → `uvicorn` fails immediately
- Missing `.venv` or Python path misconfiguration

---

## Reference

- Existing service template: `infra/systemd/depthfusion-rest.service` (REST API, port 7300)
- Auth env vars: `DEPTHFUSION_V2_LEGACY_AUTH=1` + `DEPTHFUSION_API_TOKEN` (see ADR-S191)
- MCP server entry point: `depthfusion.mcp.http_server:app` on port 7301
