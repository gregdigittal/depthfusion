# DX Review — agent-ops Handoff Response

> **Reviewer:** Claude (Opus 4.7, 1M ctx) running `/plan-devex-review` framework
> **Mode:** DX TRIAGE (critical gaps only — agent-ops is starting consumption now)
> **Artifact under review:** `docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md`
> **Other artifacts (out of scope, not developer-facing):** the dogfood report and BACKLOG.md edits — internal docs, no DX surface to score
> **Status:** DONE_WITH_CONCERNS — the handoff is good, with five concrete gaps that will cost agent-ops time at minute 5–30 of their integration

---

## Developer persona

agent-ops engineer with ~3 days budgeted to consume the new API surface. Has never read DepthFusion source. Will pin a SHA, copy-paste request/response shapes, write Pattern B TypeScript handlers in `packages/core/src/memory/`, hit unknown errors, debug, ship. Their success metric: their four E-04 stories close inside the 3-day budget without blocking on DF clarifications.

## Empathy narrative — what happens minute by minute

| T+ | What they do | Friction |
|---|---|---|
| 0 | Read TL;DR. See "GO with caveat. Pin SHA `25fd205`." | None — clear verdict, fast orientation |
| 2 | Open §2.1 `pin_discovery` (smallest tool, easiest first call) | None — schemas are clear |
| 4 | Hit "filename resolution: relative to the discovery root for the active project" | **FRICTION 1.** *Where is the discovery root?* Not stated. They grep DF source. ~5 min lost |
| 8 | §2.2 `set_memory_score`. Notes the max-of-confidences gotcha | None — well-flagged |
| 12 | §2.3 `recall_feedback`. Wants to see a full `recall_relevant` response so they know how to thread `recall_id` end-to-end | **FRICTION 2.** Only "relevant fields" shown. They guess or grep `mcp/server.py` |
| 18 | §2.5 `publish_context` — sees BREAKING CHANGE. Knows their existing code reads `result.item.something`. Goes hunting for those call sites | **FRICTION 3.** Doc names the pattern but not the agent-ops files. Could have said "look for `result.item` in `core/src/checkpoint/handlers.ts:tryDepthFusionPublish()`" since ADR 0004 already names it |
| 25 | §4 issue #4 (zero production-path emissions). Reads "may or may not affect agent-ops depending on how your MCP server is wired" | **FRICTION 4 — CRITICAL.** Now uncertain whether their integration will silently no-op. The doc identifies the risk but gives no diagnostic. They build a test harness from scratch (~30 min) |
| 35 | Wants to write their first call. Looks for an end-to-end snippet: install + import + call + parse | **FRICTION 5 — CRITICAL.** No copy-paste hello-world exists in the doc. They piece it together. ~15 min lost |
| 50 | Writes first `pin_discovery` call. Gets back `{"error": "file not found"}`. Wonders: did the call work and the file's missing, or did the call fail? | None — error message is clear |

**Estimated TTHW (time to first successful round-trip from agent-ops side): 30–50 minutes.** With the five fixes below: 5 minutes. The handoff has the data; it's the *path through the data* that's missing.

---

## Scores against the Seven DX Characteristics

| # | Characteristic | Score | Gap to 10 |
|---|---|---|---|
| 1 | Usable | 5/10 | Add a 60-second smoke test (see §"Magical moment" below). Currently no runnable code |
| 2 | Credible | 8/10 | Explicit SHA pin, explicit breaking change, honest known-issues. Could go to 9 with a "what we tested" subsection |
| 3 | Findable | 7/10 | Cross-references include source line numbers, good. Missing: agent-ops-side file pointers for the breaking change (ADR 0004 already names them — repeat them) |
| 4 | Useful | 9/10 | Answers every inbound question point-by-point. Best-scoring axis |
| 5 | Valuable | 7/10 | Saves them from speculation. Loses points on the "smoke test the production-path question early" advice without telling them HOW |
| 6 | Accessible | 6/10 | Python-only mental model. Agent-ops is TypeScript. Either add a curl/MCP-tool-call equivalent or acknowledge "you'll be calling these via your MCP client; here's the wire-level shape" |
| 7 | Desirable | 7/10 | "GO with caveat" framing is direct and trustworthy. Tone is right |

**Overall: 7/10.** Good handoff. Five specific fixes lift it to 9.

---

## Magical moment that's missing

A single copy-pasteable snippet at the top of §6 that lets agent-ops verify their environment works in 60 seconds, before writing any consumer code:

```bash
# 60-second smoke test: confirms DF E-27 is installed + importable + tool dispatch works
python -c "
import json
from depthfusion.mcp.server import _tool_pin_discovery
r = json.loads(_tool_pin_discovery({'filename': '__smoke_test__.md', 'pinned': True}))
assert r.get('error') == 'file not found', f'unexpected response: {r}'
print('OK — DepthFusion E-27 wired correctly. You can call all four E-27 tools.')
"
```

That single block proves: install path is right, package version contains E-27, MCP tool-dispatch layer reachable, response parses as JSON. Three of the five frictions evaporate. It is the chef-for-chefs move — agent-ops is also shipping APIs; they will appreciate not having to write this themselves.

---

## Critical gaps (fix before agent-ops starts; ~20 min of doc edits total)

1. **No runnable hello-world.** Add the 60-second smoke test above to §6 step 1.
2. **§4 issue #4 (zero production-path emissions) is alarming without being actionable.** Replace the "may or may not affect agent-ops" hand-wave with a 3-line diagnostic:
   ```bash
   # Check whether YOUR Claude Code session emits production-path events:
   ls -la ~/.claude/depthfusion-metrics/ | grep "$(date -u +%Y-%m-%d)"
   # If you see today's date with non-zero file sizes AND your project name in the events, you're fine.
   # If empty or only /tmp/ paths in events, you're affected by S-79 — file with us.
   ```
3. **Filename resolution under-specified in §2.1 / §2.2.** Add one line: "Filenames are resolved relative to `~/.claude/depthfusion-discoveries/<project>/` — pass the bare filename, not an absolute path." The doc already says "bare filename, not absolute path" but doesn't say what root.
4. **§2.3 `recall_relevant` response shows only the new fields.** Replace `// ... all pre-S-72 fields unchanged` with the actual full response — agent-ops needs to know the chunk_id field name to construct the `used`/`ignored` lists. Right now they have to guess or grep.
5. **§2.5 / §3 breaking change has no migration snippet.** Add a before/after:
   ```typescript
   // Before (v0.5.x stub response):
   const r = await mcpClient.call('depthfusion_publish_context', {...});
   const id = r.item.item_id;  // ← undefined after E-27

   // After (E-27 response):
   const r = await mcpClient.call('depthfusion_publish_context', {...});
   const id = r.item_id;
   const wasDuplicate = r.deduped;
   ```
   This compresses the ADR-0004-find-the-call-site work from "go grep" to "edit two lines."

---

## Polish (nice-to-have, can wait)

- **§4 known issues are scattered.** Consider consolidating known-issues + gotchas into a single "Common pitfalls" subsection so agent-ops has one place to scroll.
- **TTHW is currently un-stated.** A line like "Expected first round-trip: < 5 min after smoke test passes" sets a quality bar.
- **The "ping us when you cut your first E-04 release" loop in §6** is good — keep it. Creates a feedback flywheel and gives DF a forcing function for v0.6.0.
- **No code-block syntax highlighting hint for `jsonc`** in some response shapes. Minor; renderers vary.

---

## What this review is NOT saying

- Not saying the handoff is wrong. The technical content is accurate (verified against `mcp/server.py` line 22–230 + 755 + 1066 + 1163 + 1265).
- Not saying agent-ops can't consume it as-is. They can. They'll just spend 30–50 minutes doing what could be 5.
- Not saying breaking changes are the issue. The ONE breaking change is well-disclosed; the issue is migration ergonomics, not disclosure.

---

## Recommended action

Edit the handoff response to apply the five fixes (~20 minutes). Then the SAME doc serves both as the answer to agent-ops's questions AND as their getting-started guide. That's a higher-leverage edit than producing a separate quickstart later.

Alternative: leave the handoff as a reference doc and add a separate `docs/coordination/2026-05-05-e27-quickstart-for-agent-ops.md` containing the smoke test + migration snippet + diagnostic. Two files cost one context switch — fix #1 above is better.

---

## Status

**DONE_WITH_CONCERNS.** The handoff is shippable as-is. The five gaps will cost agent-ops time but won't block them. Recommend applying the five fixes before they start consuming. If we don't, log the friction back from agent-ops and apply on the v2 of the handoff after they finish their integration.
