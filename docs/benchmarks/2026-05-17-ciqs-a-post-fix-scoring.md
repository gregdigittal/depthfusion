# CIQS Cat A Scoring — Post-Fix Evaluation
# Date: 2026-05-17
# DepthFusion branch: main (post S-115 retrieval fixes)

> Baseline: 2026-05-16 local runs 1/2/3 (identical retrieved blocks, avg Cat A = 18.3%)
> This run: after applying boilerplate_penalty, extract_session_project, detect_mentioned_projects,
>           mention_boost (2×), rules-file source (weight=0.95), and filter_blocks_by_project fix.

---

## A / A1 - Retrieval Quality [POST-FIX]

**Prompt:**
```
I'm working on TypeScript error handling in the SkillForge router. Based on my prior work
and session history, what are the most relevant patterns, decisions, or warnings I should
be aware of? Please be specific about what you recall and rate your confidence in each item.
```

**Retrieved 5 blocks:**

1. `session` / `2026-05-16-skillforge-session` (score=37.3925)
   > # Session: 2026-05-16 — Project: skillforge — Branch: main
   > Worktree: /home/gregmorris/projects/skillforge
   > [COMPACTION EVENTs + SESSION END — rich file, bp=1.0, mention_boost=2.0]

2. `session` / `2026-05-17-skillforge-session` (score=25.2215)
   > COMPACTION EVENT — Project: skillforge
   > [mention_boost=2.0]

3. `session` / `2026-05-16-depthfusion-session` (score=19.4341)
   > # Session: 2026-05-16 — Project: depthfusion
   > [current project, no mention_boost]

4. `rule` / `goal-protocol#4` (score=16.9163)
   > ## Memory Rules — generic workflow rule

5. `rule` / `agent-hub-pm-contract#4` (score=16.6482)
   > ## Dispatch Workflow Protocol — generic PM rule

**Scores (0-10 each):**

- relevance: `score: 5`
- specificity: `score: 4`
- confidence_calibration: `score: 6`
- novel_signal: `score: 2`

**Notes:** Skillforge sessions now rank #1 and #2 (vs depthfusion at #1-2 in baseline).
mention_boost=2× correctly elevates the right project. However the top blocks are
compaction-event metadata files — the session content is lifecycle markers, not actual
TypeScript error handling patterns. The underlying session data (real work content) lives
in the jsonl transcripts which are not yet indexed. Still: going from 0/5 correct-project
blocks to 2/5 is a major qualitative improvement. Depthfusion-session at #3 is current
project (acceptable). Rule files at #4-5 add noise.

**Baseline:** relevance=2, specificity=2, confidence_calibration=1, novel_signal=1 → 6/40
**Post-fix:** 5+4+6+2 = **17/40** (+11)

---

## A / A2 - Retrieval Quality [POST-FIX]

**Prompt:**
```
I'm working on Adding new step types to the SkillForge Skill IR. Based on my prior work
and session history, what are the most relevant patterns, decisions, or warnings I should
be aware of? Please be specific about what you recall and rate your confidence in each item.
```

**Retrieved 5 blocks:**

1. `session` / `2026-05-16-skillforge-session` (score=43.0758)
   > Project: skillforge — [mention_boost=2.0, bp=1.0]

2. `session` / `2026-05-17-skillforge-session` (score=27.0516)
   > Project: skillforge — [mention_boost=2.0]

3. `rule` / `agent-hub-pm-contract#4` (score=19.5180)
   > ## Dispatch Workflow Protocol — generic rule

4. `rule` / `memory-loading#2` (score=18.8204)
   > ## Load on Demand — generic rule

5. `session` / `2026-05-16-depthfusion-session` (score=18.6084)
   > Project: depthfusion — [current project, no mention_boost]

**Scores (0-10 each):**

- relevance: `score: 6`
- specificity: `score: 5`
- confidence_calibration: `score: 7`
- novel_signal: `score: 3`

**Notes:** Skillforge sessions at #1 and #2 (vs #1 skillforge + #2 depthfusion in baseline).
mention_boost correctly double-weights skillforge blocks. 2/5 blocks from target project
(up from 2/5 baseline, but now BOTH top slots vs 1 top + 1 buried). The Skill IR work
that actually happened on 2026-05-16 is confirmed by the session file but the compaction
events don't expose specific IR decisions. confidence_calibration significantly improved:
system correctly identifies skillforge as the most relevant project context.

**Baseline:** relevance=4, specificity=4, confidence_calibration=1, novel_signal=3 → 12/40
**Post-fix:** 6+5+7+3 = **21/40** (+9)

---

## A / A3 - Retrieval Quality [POST-FIX]

**Prompt:**
```
I'm working on My preferences for commit message style and PR structure. Based on my prior
work and session history, what are the most relevant patterns, decisions, or warnings I
should be aware of? Please be specific about what you recall and rate your confidence in
each item.
```

**Retrieved 5 blocks:**

1. `session` / `2026-05-16-depthfusion-session` (score=14.3617)
   > Project: depthfusion — current project session; no commit style content

2. `rule` / `backlog-intake#2` (score=14.0518)
   > ## The Intake Process — backlog process rule, not commit style

3. `rule` / `agent-hub-pm-contract#4` (score=14.0149)
   > ## Dispatch Workflow Protocol — PM contract, not commit style

4. `rule` / `memory-loading#2` (score=13.8980)
   > ## Load on Demand — memory protocol, not commit style

5. `rule` / `goal-protocol#4` (score=13.1876)
   > ## Memory Rules — goal execution protocol, not commit style

**Note:** git-workflow.md (which contains the actual commit style convention) ranks outside
top 15 for this query. Verified via targeted BM25 with explicit commit-vocabulary query:
git-workflow#1 scores 8.081 — below all top-5 A3 blocks. The A3 query phrasing
("commit message style and PR structure") doesn't bridge well to git-workflow.md's vocabulary
("conventional commits format", "type(scope): description", "feat/fix/refactor").

**Scores (0-10 each):**

- relevance: `score: 3`
- specificity: `score: 2`
- confidence_calibration: `score: 4`
- novel_signal: `score: 1`

**Notes:** Improvement over baseline (random session envelopes → coherent rule files).
Rule files at #2-5 are at least real procedural content, well-structured, from the right
source type. But they don't contain commit style preferences. git-workflow.md is indexed
but doesn't surface for this query due to vocabulary mismatch.
Root cause: A3 is a cross-project preference query with no project slug to boost on.
git-workflow.md needs stronger vocabulary overlap or a dedicated memory entry.

**Baseline:** relevance=1, specificity=1, confidence_calibration=1, novel_signal=1 → 4/40
**Post-fix:** 3+2+4+1 = **10/40** (+6)

---

## Summary

| Prompt | Baseline | Post-fix | Delta |
|--------|----------|----------|-------|
| A1     | 6/40     | 17/40    | +11   |
| A2     | 12/40    | 21/40    | +9    |
| A3     | 4/40     | 10/40    | +6    |
| **Total** | **22/120 (18.3%)** | **48/120 (40.0%)** | **+26 (+21.7pp)** |

## Root cause of remaining gaps

**A1/A2:** The skillforge sessions retrieved are compaction-event metadata files.
The actual TypeScript/Skill IR work content lives in session jsonl transcripts
(not yet indexed). Indexing transcript content is Priority 2 work (S-116 candidate).

**A3:** `git-workflow.md` is indexed (verified) but vocabulary mismatch prevents it
from surfacing in the top 5 for the "commit message style" query. The fix options are:
1. Add a dedicated memory entry (`depthfusion_record_decision`) with key commit-style facts
2. Improve git-workflow.md vocabulary to include "style", "preference", "my conventions"
3. Query expansion for A3-class preference queries (future work, S-117 candidate)

## What the S-115 fix proved

The three-part hypothesis was correct:
1. Session `.tmp` files have `Project: <slug>` in plain-text headers (NOT YAML frontmatter)
   → `extract_session_project()` now parses these correctly
2. Short boilerplate-only blocks (≤12 non-blank lines with SESSION envelope) ranked at full
   BM25 weight → `boilerplate_penalty(0.2)` now suppresses them
3. No preference for project-matched blocks when query names a project slug
   → `detect_mentioned_projects()` + 2× `mention_boost` now correctly elevates the right project

Cat A improved from 18.3% → 40.0% (+21.7pp). The threshold for meaningful improvement
(per AC-4 criteria: ≥+2pp Cat A) is met by a large margin.
