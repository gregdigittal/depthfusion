# Tailscale Setup for the Shared Memory Fabric

This guide walks through exposing DepthFusion's Event Graph Fabric endpoints on
your Tailscale network so multiple agents on different machines can share memory
in real time.

---

## Prerequisites

- DepthFusion installed and working in `vps-cpu` or `vps-gpu` mode
- A Tailscale account and the `tailscale` CLI installed on the server
- A strong random API token (generate with `openssl rand -hex 32`)

---

## Step 1: Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Verify your Tailscale IP (the `100.x.x.x` address):

```bash
tailscale ip -4
# Example output: 100.64.0.12
```

---

## Step 2: Install the `fabric` extra

The Fabric layer requires `redis>=5.0` for the live stream backend:

```bash
pip install "depthfusion[fabric]"
```

If you don't have Redis or don't need the live SSE stream (graph-only mode
is sufficient for most use cases), you can skip this — the server degrades
gracefully without Redis.

---

## Step 3: Configure the systemd service

Edit your `~/.config/systemd/user/depthfusion-rest.service` (created during
the base install) to add the Tailscale and token env vars:

```ini
[Service]
Environment="DEPTHFUSION_API_TAILSCALE=1"
Environment="DEPTHFUSION_API_TOKEN=<your-token-here>"

# Optional: point at a running Redis instance for live SSE streaming
# Environment="DEPTHFUSION_REDIS_URL=redis://127.0.0.1:6379"
```

**Security note:** `DEPTHFUSION_API_TOKEN` is required whenever
`DEPTHFUSION_API_TAILSCALE=1` is set. The server refuses to start without it.
Redis (if configured) always binds loopback-only — it is never exposed on the
Tailscale interface.

---

## Step 4: Reload and start

```bash
systemctl --user daemon-reload
systemctl --user restart depthfusion-rest
systemctl --user status depthfusion-rest
```

The server logs its bind addresses at startup:

```
INFO: Uvicorn running on http://127.0.0.1:7300
INFO: Tailscale bind: http://100.64.0.12:7300
```

---

## Step 5: Verify connectivity

From another machine on the same Tailscale network:

```bash
export TOKEN=<your-token-here>
export TS_IP=100.64.0.12   # replace with your server's tailscale ip

# Health check
curl -s -H "Authorization: Bearer $TOKEN" http://$TS_IP:7300/v1/events/seed?projects=test
# → {"memories":[],"degraded":false,...}

# Publish a test event
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"verify-agent","project_slug":"test","memory_refs":["check-1"]}' \
  http://$TS_IP:7300/v1/events/publish
# → {"event_id":"...","indexed":true}

# Confirm provenance
curl -s -H "Authorization: Bearer $TOKEN" \
  http://$TS_IP:7300/v1/graph/agent/verify-agent/trail?project=test
# → {"trail":[{"entity_id":"...","event_type":"AGENT_PUBLISHED",...}],"count":1}
```

---

## Firewall note

Tailscale handles its own ACLs. If you want to restrict which Tailscale nodes
can reach port 7300, add an ACL rule in the Tailscale admin console:

```json
{
  "action": "accept",
  "src": ["tag:agent"],
  "dst": ["tag:depthfusion-server:7300"]
}
```

The loopback listener (`127.0.0.1:7300`) is always available for local tools
regardless of Tailscale ACLs.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ValueError: DEPTHFUSION_API_TOKEN required` at startup | Token not set | Add `Environment="DEPTHFUSION_API_TOKEN=..."` to the service file |
| `WARNING: Tailscale IP resolution failed` in logs | `tailscale` binary not in PATH | `sudo ln -s $(which tailscale) /usr/local/bin/tailscale` |
| 401 from remote agents | Token mismatch | Ensure all agents use the same token value |
| SSE stream disconnects after ~60s | nginx/reverse-proxy buffering | Add `proxy_buffering off; proxy_read_timeout 3600s;` to your nginx config |
