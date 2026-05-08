---
date: 2026-05-08
from: depthfusion
to: agent-ops
re: E-27 cleared for consumption
status: ready-to-send
---

# To: agent-ops — DepthFusion E-27 cleared for consumption

E-27 (Memory Policy Layer) is ready. The handoff doc is at:

`docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md`

## Pin SHA

**Pin SHA:** `2f6b212` (latest on `main` as of 2026-05-08).

Do **not** use the `v0.6.0a1` tag — E-27 landed after that tag was cut, and additional E-29 polish improvements have landed since the original handoff. The E-27 contract surface (the four MCP tool response shapes) is **unchanged** since the original handoff doc — see "What changed since the handoff" below for the full picture.

## Before writing any consumer code

Run the §6 smoke test in the handoff doc (takes ~60 seconds):

```bash
python -c "
import sys; sys.path.insert(0, 'src')
from depthfusion.mcp.server import DepthFusionServer
import json
s = DepthFusionServer.__new__(DepthFusionServer)
s.__init__()
# pin_discovery
r = s._tool_pin_discovery('/home/gregmorris/.claude/shared/discoveries/any-existing.md')
print('pin_discovery:', json.loads(r)['pinned'])
"
```

The full smoke test (all 4 tools with expected response shapes) is in §6 of the handoff doc.

## What to update on your end

- Mark **S-12, S-14, S-15, S-16** as unblocked in your BACKLOG.md
- Update **ADR 0004**: `conditional` → `authorized`
- The breaking change to watch: `publish_context` response shape changed from `{published, item: object}` to `{published, item_id, deduped}`. Update `core/src/checkpoint/handlers.ts:tryDepthFusionPublish()` accordingly — see §3 of the handoff doc for the migration note.

## Known limitations to design for

`RecallStore` is **process-local in-memory**. It does not survive MCP server restarts, so `recall_feedback` references expire if the server restarts between `recall_relevant` and `recall_feedback` calls. Design consumer flows assuming `skipped_missing` is possible even with a valid `recall_id`.

## What changed since the handoff was first drafted (2026-05-05 → 2026-05-08)

The E-27 contract surface (the four MCP tool response shapes documented in §2) is **unchanged**. The following follow-up work landed under E-29 (v0.5.3 polish), all in `main`:

| Story | Effect on agent-ops | Action required |
|---|---|---|
| S-79 AC-3 | MCP server now emits `system.startup` event into the legacy metrics stream on init | None — internal observability only |
| S-80 | All six retrieval capabilities now appear in `latency_ms_per_capability` (was reranker-only) | None — JSONL stream only, not MCP response |
| S-81 | `config_version_id` now populated in capture/recall events (was empty string) | None — JSONL stream only, not MCP response |
| S-82 | Test telemetry routed to tmp dirs, no longer pollutes `~/.claude/depthfusion-metrics/` | None — test-only |
| S-83 | `backend_fallback_chain` now populated in recall events with per-query cascade trace | None — JSONL stream only, not MCP response |
| S-84 | Runbook docs corrections | None — docs only |

Plus three documentation corrections to the handoff doc itself (commits `1fca21c` and `54d8e6d`):

- §2.3 `recall_relevant` block fields — `source` is a label (`"session"` / `"discovery"` / `"memory"`), not a path
- §2.3 empty-result shape — `total_sources_scanned` is **absent** on empty paths, not `0`
- §2.3 per-block caveat — `gate_b_score`, `gate_c_score`, `gate_fused_score` appear when `DEPTHFUSION_FUSION_GATES_ENABLED=true` (off by default)

If your team already started reading the handoff before 2026-05-08, re-read §2.3 and §6 — those are where the corrections landed.
