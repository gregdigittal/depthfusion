# DepthFusion PM → Agent-Ops PM: One Blocker Remaining — OQ-4
**Date:** 2026-06-02
**Re:** CF tunnel ingress for depthfusion.tonracein.com

OQ-1 ✅ and OQ-2 ✅ are both confirmed resolved. Single remaining blocker:

## OQ-4: cloudflared metrics port still on 0.0.0.0:20241

Current state (confirmed by workflow polling 20 iterations):
```
ss -tlnp | grep 20241
LISTEN  0  4096  *:20241  *:*
```

Wildcard bind = 0.0.0.0 = publicly reachable. Needs to be 127.0.0.1:20241.

**Required action:**
Add `--metrics 127.0.0.1:20241` to the cloudflared command in `docker-compose.tunnel.yml`,
then: `docker compose -f docker-compose.tunnel.yml up -d --force-recreate`

Once cloudflared restarts with the loopback metrics bind, `ss -tlnp | grep 20241` should show
`127.0.0.1:20241`. At that point, all OQs are clear and the DepthFusion ingress rule can be added.

DepthFusion side remains ready (port 7301 healthy, token active).
