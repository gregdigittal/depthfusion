---
**Document Control**

| Field | Value |
|---|---|
| Project | DepthFusion |
| Document | MCP Stability Diagnosis — Adversarial Multi-Agent Analysis |
| Author | Greg Morris |
| Version | v1.0 |
| Date | 06-08-2026 |
| Status | Draft |
| Method | Fable 5 + Codex adversarial workflow (5 agents, 174 tool calls, 724k tokens) |
---

## Executive Summary

**Verdict: Currently FRAGILE with accidental recovery only.** Every automated safety net —
the watchdog kill path, the sudo fallback, the health check cadence, and the systemd restart
limits — is non-functional. MTTR is unbounded. The Aug 6 outage lasted 3h 45m and ended only
because an unrelated human action stopped the SSE client holding the connection open.

The two MCP surfaces have different failure modes:

| Surface | Problem | Fix |
|---|---|---|
| HTTP service (`depthfusion-mcp.service`) | Graceful shutdown hangs indefinitely; watchdog can't recover it | P1 + P2 + P6 (35 min of work) |
| stdio MCP (Claude Code sessions) | Exceptions drop the JSON-RPC response → Claude Code times out → forces `/mcp` reconnect | P3 + P4 |

**After P1–P2 (35 minutes):** any HTTP hang is bounded at ~5 minutes. This alone resolves
the core complaint for the HTTP surface.

**After P3–P4 (2.5 hours):** stdio flakiness, zombie accumulation, and the memory-pressure
feedback loop are fixed.

**After P5–P7 (4 hours more):** automated detection within 90s, automated recovery within
~2 min, no unbounded failure modes remaining.

---

## Root Causes (ranked by confidence)

### #1 — Every automated recovery layer is broken [High confidence]

The watchdog's unhealthy branch calls `pkill SIGTERM depthfusion-mcp`, but uvicorn running
with live SSE connections ignores SIGTERM until all connections close — by design. This
means 40+ SIGTERM attempts achieved nothing. The fixed branch (`sudo systemctl restart`) was
there but unreachable because the sudoers rule expects the unit name `depthfusion-mcp` while
the script passes `depthfusion-mcp.service` — an exact-argument mismatch that returns 1
and drops into the "NEEDS_MANUAL_RESTART" branch, logging that human intervention is required
and exiting silently. The health check only runs twice a day. The result: any hang becomes
a multi-hour outage that ends only by accident.

### #2 — Shutdown-hang trap in the HTTP server [High confidence]

`uvicorn.run()` at `http_server.py:810` passes no `timeout_graceful_shutdown` (default =
wait forever). The `/sse` and `/mcp` streaming endpoints loop forever, emitting `: ping`
every 30s; they only stop if the **client** disconnects. Fable's adversarial challenger
identified via access logs that the Aug 6 hang was held open by a kitabu container at
`172.21.0.5` (kitabu's `_default` docker network), not a Claude Code session. The hang
lasted until `docker compose down` stopped the kitabu containers ~7 min before the
restart. One line in `http_server.py:810` would have bounded this at 30 seconds.

### #3 — Resource exhaustion + silent error paths [High confidence, mechanism disputed]

Pre-SIGTERM, the host was at 97% swap utilisation (30/31 GiB used), fed partly by zombie
stdio MCP processes that could not be killed because of the HNSW SIGTERM handler (F1 below).
Each zombie holds a loaded sentence-transformers embedding model (~400 MB RAM each). Memory
pressure stalled the uvicorn event loop for 5–8 minutes at a stretch from ~04:56 UTC,
causing the watchdog to fire — correctly detecting a slow-but-alive failure — but then
recovering incorrectly via pkill (root cause #1). Without fixing the zombie source and host
memory limits, P1–P6 will just make the service restart faster while the stalls recur.

---

## Action Plan

### P1 — Cap graceful shutdown · `Critical` · 5 min

**File:** `src/depthfusion/mcp/http_server.py:810`

One-line change. Forces uvicorn to cancel in-flight SSE responses 30s after SIGTERM.
**This single change would have reduced the Aug 6 outage from 3h 45m to ~35s.**
Both Fable and Codex agree; zero risk.

```python
# http_server.py:810
uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=30)
```

---

### P2 — Fix the watchdog · `Critical` · 30 min

**Files:** `scripts/mcp-health-check.sh` + sudoers rule

The NOPASSWD rules already exist. The script just passes the wrong argument.
Replace `pkill` with `systemctl restart`, fix the unit name mismatch.

```bash
# scripts/mcp-health-check.sh — in the "Both checks failed" branch:
# OLD (broken):
if sudo -n systemctl restart depthfusion-mcp.service 2>/dev/null; then

# NEW (matches existing NOPASSWD rule exactly):
if sudo -n systemctl restart depthfusion-mcp 2>/dev/null; then

# Also remove the pkill SIGTERM line entirely — it is provably ineffective.
```

With P1 + P2 together, any future hang is bounded at one health-check interval. Until
P5 (timer), that interval is still 6 hours worst-case; shipping P5 next makes this ~5 min.

---

### P3 — Delete `_register_hnsw_shutdown()`; use `atexit` · `Critical` · 30 min

**File:** `src/depthfusion/mcp/tools/_state.py:53-78`

The current HNSW shutdown handler calls only `store.save()` — no `sys.exit()`, no re-raise,
no chain to the previous handler. SIGTERM is therefore swallowed. In the stdio server,
tools dispatch on the main thread, satisfying the main-thread check at `_state.py:71`, so
every Claude Code session that loads the HNSW store becomes unkillable after first use —
requiring SIGKILL to clean up. These zombies are the source of the memory pressure in RC#3.

```python
# _state.py — remove _register_hnsw_shutdown() and all signal.signal() calls.
# Replace with:
import atexit
atexit.register(lambda: _HNSW_STORE.save() if _HNSW_STORE else None)

# HTTP server lifespan finally block (http_server.py ~line 102):
if _HNSW_STORE:
    try:
        _HNSW_STORE.save()
    except Exception as exc:
        logger.warning("lifespan: hnsw flush failed: %s", exc)
```

*(Note: Codex and Fable disputed whether the handler was actually installed in the HTTP
process — HTTP tool dispatch uses `run_in_executor` worker threads, not the main thread,
so the install condition at `_state.py:71` would not be satisfied. Both agents agree on
deleting it regardless, and it is unambiguously the stdio zombie cause.)*

---

### P4 — stdio loop: always respond; exit on BrokenPipeError · `High` · 2h

**File:** `src/depthfusion/mcp/server.py:668-686, :499`

Today any unexpected exception in `_process_request` drops the response entirely. Claude
Code waits for a reply with id=N that never arrives, hits its per-request timeout (~30s),
and marks the server dead — forcing `/mcp` reconnect. The fix is a protocol-compliant
error response in the catch-all, plus an explicit exit on broken pipe.

```python
# server.py main() loop — replace the outer except block:
except BrokenPipeError:
    # Transport is dead; exit cleanly.
    sys.exit(0)
except Exception as exc:
    logger.error("unhandled exception processing request: %s", exc, exc_info=True)
    if req_id is not None:
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": f"Internal error: {exc}"}
        }), flush=True)
    # continue — don't exit; the next request may succeed

# server.py _process_request ~line 499 — validate params type:
if not isinstance(params, dict):
    raise McpError(-32602, "params must be an object")
```

---

### P5 — Health check every 5 minutes via systemd timer · `High` · 30 min

**Files:** new `infra/systemd/depthfusion-health.timer` + `.service`

Replace the 2×/day cron with a root-privileged systemd timer. Running as root eliminates
the sudo dependency entirely. Also fix the `curl '000000'` artifact (the `|| echo 000`
fires after `-w "%{http_code}"` has already written `000` — produces `000000` not `000`).

```ini
# infra/systemd/depthfusion-health.timer
[Unit]
Description=DepthFusion MCP Health Check (every 5 min)
[Timer]
OnCalendar=*:0/5
Persistent=true
[Install]
WantedBy=timers.target

# infra/systemd/depthfusion-health.service
[Unit]
Description=DepthFusion MCP Health Check
[Service]
Type=oneshot
ExecStart=/home/gregmorris/projects/depthfusion/scripts/mcp-health-check.sh
User=root
```

```bash
sudo systemctl enable --now depthfusion-health.timer
sudo crontab -e  # remove the existing 0 6,18 * * * cron line
```

---

### P6 — WatchdogSec + sd_notify · `High` · 2h

**Files:** `infra/systemd/depthfusion-mcp.service` + `http_server.py`

The Aug 6 initiating failure was an event loop stalled by memory pressure — the process
was alive (`/health` eventually returned 200) but responses came minutes late. The watchdog
correctly detected this but recovered incorrectly. `WatchdogSec=90` with `sd_notify` from
the asyncio event loop is the one directive that catches "slow-but-alive" failures that
health checks and crash-restart loops miss.

```ini
# infra/systemd/depthfusion-mcp.service additions:
[Service]
Type=notify
WatchdogSec=90
TimeoutStopSec=60
```

```python
# http_server.py lifespan startup: start an asyncio task that calls
# sdnotify.SystemdNotifier().notify("WATCHDOG=1") every 30s.
# Also call notify("READY=1") after startup completes.
```

---

### P7 — Relieve host memory pressure · `High` · 2h (ops)

**Scope:** host operations + docker compose files for `dm-agent-*` containers

Swap at 97% is what stalled the event loop. P3 stops new zombies accumulating; this cleans
the 12 existing ones and prevents co-tenant LLM containers from starving depthfusion again.

```bash
# One-time cleanup (AFTER P3 ships):
pkill -9 -f 'depthfusion.mcp.server'

# docker compose files: add mem_limit to the 9 LLM-agent containers
# Example:
services:
  dm-agent:
    mem_limit: 4g
    memswap_limit: 4g  # disables swap for this container

# Alerting: node_exporter + alertmanager rule on node_memory_SwapFree < 20%
```

---

### P8 — SSE generators observe app shutdown event · `Medium` · 30 min

**File:** `src/depthfusion/mcp/http_server.py:451-462, 510-525`

Cooperative stream termination — belt-and-braces on top of P1's force-cancel.

```python
# lifespan startup:
app.state.shutting_down = asyncio.Event()
# lifespan finally:
app.state.shutting_down.set()

# SSE generators — replace `while True:` with:
while not app.state.shutting_down.is_set():
    try:
        chunk = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL)
        yield chunk
    except asyncio.TimeoutError:
        yield ": ping\n\n"
```

---

### P9 — Fix restart limits + enable app logging to journald · `Medium` · 30 min

**File:** `infra/systemd/depthfusion-mcp.service` + `http_server.py`

Current `StartLimitBurst=5` in 30s with `RestartSec=5` means 5 fast failures permanently
stops the unit — exactly the scenario after memory-pressure OOM crashes. The fix widens the
window. App INFO logs not reaching journald materially impeded this investigation.

```ini
# infra/systemd/depthfusion-mcp.service:
StartLimitIntervalSec=300  # was 30
RestartSec=10              # was 5
```

```python
# http_server.py or server.py top-level:
import logging
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
```

---

### P10 — Do NOT rebind port 7301 to 127.0.0.1 yet · `Low` · 1 day (prerequisite)

**File:** `http_server.py` bind + nginx + kitabu compose

The infra investigation proposed binding 7301 to loopback for security. Fable's challenger
showed via `docker inspect` that kitabu containers consume `host:7301` directly (not via
nginx). A loopback rebind would silently break kitabu. Track as a story; do it deliberately
once container consumers are migrated to the nginx endpoint or a shared docker network.

---

## What "MCP going down" actually means (two distinct things)

| User sees | What it is | Expected? | Fix |
|---|---|---|---|
| `/mcp` reconnect during a session | stdio server received an unhandled exception, sent no response, Claude Code timed out | **No** — should be recoverable | P4 |
| `/mcp` reconnect after Claude Code restarts | stdio process died with the session | **Yes** — this is transport design | Set expectation; not a defect |
| MCP tools unavailable for hours | HTTP service hung waiting for SSE connection to close | **No** | P1 + P2 |

---

## Disputed Items

- **HNSW handler in HTTP process:** Codex claimed it replaced uvicorn's SIGTERM handler;
  Fable showed HTTP tools dispatch via `run_in_executor` (not main thread), so the
  install condition at `_state.py:71` is not satisfied. Unresolved mechanism; both
  agree the handler should be deleted.

- **Who held the SSE connection:** Codex said "likely Claude Code session"; Fable proved
  via access logs it was `172.21.0.5` (kitabu container). Changes P10 (don't rebind 7301).

- **sudo failure cause:** Codex diagnosed missing sudoers rule; Fable showed existing
  NOPASSWD rules and identified the exact-argument mismatch as the cause.

- **TimeoutStopSec as key fix:** Codex proposed it as a primary fix; Fable showed it
  wouldn't have helped this incident (the kill came from pkill outside systemd).
  Kept as defence-in-depth in P6.

- **F2 zombie framing:** Normal session termination closes stdin → loop exits cleanly
  at EOF. The zombie scenario (unkillable stdio process) belongs to F1 (HNSW handler),
  not to the dropped-response bug.

---

## Version History

| Version | Date | Author | Changes |
|---|---|---|---|
| v1.0 | 06-08-2026 | Greg Morris | Initial release — adversarial workflow output |
