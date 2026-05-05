# Request to DepthFusion — confirm E-27 ready for agent-ops consumption

- **From:** agent-ops (Greg / Claude)
- **To:** DepthFusion project
- **Date:** 2026-05-05
- **Status:** responded — see `docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md` (verdict: GO with one caveat — pin by SHA `25fd205`, not version tag)
- **Priority:** medium — agent-ops has 4 stories ready to start as soon as we have confirmation

## Why we're writing

Per the agent-ops `BACKLOG.md` cross-cutting dependency map, four agent-ops E-04 stories and one architectural decision (ADR 0004) were filed as **blocked on DepthFusion E-27**:

| agent-ops item | DepthFusion blocker |
|---|---|
| E-04 S-12 — set/read/update importance & salience | DF E-27 S-70 |
| E-04 S-14 — pin / unpin discoveries | DF E-27 S-69 |
| E-04 S-15 — recall feedback from checkpoint publishes | DF E-27 S-72 |
| E-04 S-16 — Slack notification on high-importance publishes | DF E-27 S-73 |
| ADR 0004 — DepthFusion publish retry code | DF E-27 S-78 |

A backlog status sweep on **2026-05-05** found that **DepthFusion E-27 is tagged `[done]`** and all five blocker stories show full AC coverage (S-69 4/4, S-70 5/5, S-72 6/6, S-73 5/5, S-78 7/7). Before we start consuming the new APIs from the agent-ops side, we want explicit confirmation from the DepthFusion side, plus a few specifics that BACKLOG ticks alone don't tell us.

## What we need to know

### 1. Released vs ticked

Are the implementations of S-69, S-70, S-72, S-73, and S-78 **released** — that is, present in the DepthFusion package version that agent-ops would consume — or are they merged-but-pre-release? If the latter, what's the expected release date?

If DepthFusion ships as a pinned dependency, please tell us the exact version (or commit SHA) that includes all five stories.

### 2. Stable public-API surface for each

For each of the five stories, please confirm the **MCP tool name** and the **input/output JSON shape** that agent-ops should code against. Best case: a one-page reference per tool. We need this to write the consuming code without speculating about the contract.

Specifically:

- **S-69 (pin discoveries):** name of the pin op (`depthfusion_pin_discovery`?), name of the unpin op, input shape (filename only, or content_hash, or both?), idempotency semantics (re-pinning a pinned discovery — no-op or error?).
- **S-70 (importance + salience scalars):** name of the set/read ops (`depthfusion_set_memory_score`?), value range and type (0–1 float? 0–10 int?), whether `importance` and `salience` are independent inputs or computed from a single primitive.
- **S-72 (recall feedback):** name of the feedback op (`depthfusion_recall_feedback`?), input shape — does it take recall_id + feedback_signal, or recall_id + per-chunk verdicts (useful / not useful / harmful)?
- **S-73 (high-importance publish event):** the **structured event shape** — what fields are emitted, on what channel (Postgres NOTIFY? webhook? log line we tail?), and what's the threshold (importance > N triggers? configurable?).
- **S-78 (idempotent publish_context by content_hash):** what does the response look like on a duplicate-publish (same content_hash already in store)? Returns the existing row's id silently, or an explicit `already_exists: true` flag?

### 3. Migration notes / breaking changes

Did E-27 ship any breaking changes to the existing DepthFusion MCP surface that agent-ops already uses (e.g. the existing `depthfusion_publish_context`, `depthfusion_recall_relevant`)? If so, please list them. We want to land any required call-site updates in the same PR that consumes the new tools — not as a follow-up surprise.

### 4. Known issues to be aware of

Any rough edges discovered post-merge? Anything we should defensively code around? (e.g. "S-70's salience field is computed but only updated on next access — first read may show stale data" — that kind of thing.)

### 5. Are you actively iterating on E-27?

If E-27 is "done but evolving" (small fixes, polish PRs in flight), we'd rather wait a few days for the dust to settle than have to re-version pin shortly after consuming. If it's stable, we'll start now. Please tell us which.

## What we'll do once we have the answers

In rough sequence:

1. Update agent-ops `BACKLOG.md` cross-cutting deps to mark each of S-12, S-14, S-15, S-16 as **unblocked** (currently still tagged "blocked on DepthFusion E-27 S-NN").
2. Update `docs/decisions/0004-depthfusion-publish-retry.md` from "Accepted (conditional on DF S-78 shipping)" to "Accepted (DF S-78 shipped, retry code authorized)."
3. Implement the four E-04 stories — each has a Pattern B handler in `packages/core/src/memory/` (or similar) plus CLI subcommands. Approximate effort: each is `S` size, so ~hours per story.
4. Implement the ADR 0004 retry path in `core/src/checkpoint/handlers.ts:tryDepthFusionPublish()` (small — currently a single-attempt swallow-errors call).

Estimated total agent-ops-side effort once unblocked: **2–3 days** for all four stories + ADR 0004 retry code. We're holding off until we have your confirmation so we don't code against a moving target.

## Where to reply

Please reply by editing this file (add a `## Response` section at the bottom) or by filing a new file at `~/projects/depthfusion/docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md` with the same structure. We'll watch both paths.

Cross-reference for your records:

- agent-ops `BACKLOG.md` E-04 (the four stories), cross-cutting notes section
- agent-ops `docs/decisions/0004-depthfusion-publish-retry.md`
- DepthFusion `BACKLOG.md` E-27 (all five blocker stories)
