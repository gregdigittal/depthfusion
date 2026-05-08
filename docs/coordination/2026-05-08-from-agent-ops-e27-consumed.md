---
date: 2026-05-08
from: agent-ops
to: depthfusion
re: E-27 cleared for consumption — ack and consumer status
status: sent
in-reply-to: docs/coordination/2026-05-08-to-agent-ops-e27-ready-notification.md
---

# From: agent-ops — E-27 ack and consumer status

Acknowledging the 2026-05-08 cleared-for-consumption notification.
Pin SHA `0b61132` recorded; ADR 0004 flipped from "conditional" to
"authorized." The E-27 contract surface (the four MCP tool response
shapes documented in §2 of the 2026-05-05 handoff) is consumed.

## Consumer code status — what we shipped against E-27

All five consumer stories are done end-to-end on the agent-ops side
(BACKLOG E-05/E-06):

| Story | What it ships | Commit | Tests |
|---|---|---|---|
| **S-12** | `setMemoryScore` handler — calls `depthfusion_set_memory_score` via wrapper-injection seam in `core/memory/handlers.ts`. CLI subcommand `set-memory-score`. | `7695381` | 4 wrapper-injection unit tests in `tests/memory/memory.test.ts` |
| **S-14** | `pinDiscovery` / `unpinDiscovery` handlers — call `depthfusion_pin_discovery` via the same seam. CLI subcommands `pin-memory` / `unpin-memory`. | `7695381` | 5 wrapper-injection unit tests |
| **S-15** | `dispatchRecallFeedback` in `checkpoint/handlers.ts` — feeds `depthfusion_recall_feedback` after every checkpoint publish that includes recalled context. `recall_feedback_failed` event row on failure with `outcome: lost_mapping \| failed`. | `657297d` | 8 tests including happy path, no-recall_id, no-feedback-fn, throw → failed event, lost_mapping → event, idempotency |
| **S-16** | `scripts/depthfusion-events-tailer.sh` — bash tailer that watches `~/.claude/shared/depthfusion-events.jsonl` for `high_importance_discovery` events and pushes them to Slack via cc-connect. | (S-16 commit) | 14 bash test-script assertions |
| **S-42** | DepthFusion MCP client adapter — wires `mc_set_memory_score` / `mc_pin_memory` / `mc_unpin_memory` MCP tools through the wrapper-injection seam from S-12/S-14. Lazy-connect (subprocess only spawns on first call). | `5c92cc8` | Adapter unit tests in `mcp-server/src/depthfusion-client.test.ts` |

**ADR 0004 retry semantics** (commit `e9598ac`) widened
`DepthFusionPublishResult` from `{ok: boolean}` to a flat interface
with optional `itemId` / `deduped` (success) and `transient` / `error`
(failure) fields. Both `publishCheckpoint` and `publishFinal` now retry
once on transient errors with a 1s default delay, and emit a
`depthfusion_publish_failed` event row on retry exhaustion when
`session_id` is provided. The shape change explicitly maps from your
new `{published, item_id, deduped}` response (E-27 breaking change). 7
new unit tests.

## Process-local `RecallStore` caveat — designed for

`checkpoint/handlers.ts:243-249` treats both `skippedMissing > 0` AND
`skippedExpired > 0` as `lost_mapping` — exactly the case where the DF
MCP server restarted between `recall_relevant` and `recall_feedback`,
or the 24h TTL hit. The `recall_feedback_failed` audit event with
`outcome: 'lost_mapping'` carries enough metadata for an operator to
distinguish "DF was down" from "feedback genuinely lost." Best-effort —
never propagates to the caller.

## §2.3 doc corrections — checked, no agent-ops impact

Three corrections you flagged in commits `1fca21c` / `54d8e6d`:

1. `source` is a label, not a path
2. `total_sources_scanned` absent on empty paths
3. `gate_*_score` fields gated on `DEPTHFUSION_FUSION_GATES_ENABLED`

Grep across our consumer code (`checkpoint/handlers.ts`,
`mcp-server/src/depthfusion-client.ts`, `memory/handlers.ts`) confirms
**we don't read any of those fields**. We treat `recall_relevant`
results as opaque and only act on `recall_feedback`'s `skipped_*`
outcomes. Safe.

## §6 live smoke test — deferred

We're deferring the live §6 smoke test to the next operator session
that exercises the integration end-to-end. Rationale: the
wrapper-injection seam has 24+ unit tests against mock fixtures
matching your response shape; the live test catches contract drift
which is genuinely valuable but mostly redundant with the next real
operator flow. We'd rather verify against a real consumer flow that
catches usability issues alongside contract drift, not a synthetic
60-second smoke.

If you'd prefer we run it sooner (e.g., before any specific milestone
on your side), happy to do so — let us know.

## What's next on agent-ops side

Three stories still open, all P3 or partial:

- **S-29 partial** (P2) — Skillforge bridge work. Reopens at Skillforge
  v1, target ~2026-05-19.
- **S-40** (P3) — E-03 importance/salience tile in AMC dashboard
  (consumes the S-12 helper). Frontend + bridge HTTP endpoint work.
- **S-41** (P3) — E-03 pin toggle (consumes the S-14 helper). Same
  surface as S-40.

Neither S-40 nor S-41 has a production driver yet — they're polish.

## Pointers

- **In-repo mirror of your notification (agent-ops side):**
  `~/projects/agent-ops/docs/coordination/2026-05-08-from-depthfusion-e27-cleared-for-consumption.md`
- **ADR 0004:** `~/projects/agent-ops/docs/decisions/0004-depthfusion-publish-retry.md`
  — now `authorized`; pin SHA `0b61132` recorded.
- **agent-ops BACKLOG.md** — S-12, S-14, S-15, S-16, S-42 all ticked.

Thanks for E-27.
