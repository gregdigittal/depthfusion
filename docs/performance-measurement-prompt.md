# DepthFusion — Claude Instance Performance Measurement Framework
# Date: 2026-03-28 | Status: ACTIVE

---

## Purpose

This document provides a structured evaluation protocol for measuring the **impact of individual
enhancements** (DepthFusion modules, skills, plugins, hooks, MCP servers) on your Claude Code
instance. The goal is to isolate the delta each enhancement contributes so you can make
evidence-based recommendations to other users.

---

## 1. Core Measurement Principle

Every measurement follows a **Baseline → Enhancement → Delta** structure:

```
BEFORE  →  apply enhancement  →  AFTER  →  compute delta
```

Because Claude's responses are stochastic, each prompt must be run **3 times** per condition
and the results averaged or scored with a rubric. Single measurements are not reliable.

---

## 2. Benchmark Battery

Run all 5 benchmark categories for each major enhancement. Each category tests a different
capability dimension that DepthFusion or other enhancements are likely to affect.

---

### Category A — Retrieval Quality (tests DepthFusion RRF + AttnRes weighting)

**What it measures:** Whether the MCP server surfaces the most relevant prior context, and
whether the weighting improves the signal-to-noise ratio of retrieved chunks.

**Prompt template (run 3×, vary only the topic):**

```
I'm working on [TOPIC]. Based on my prior work and session history, what are the most
relevant patterns, decisions, or warnings I should be aware of? Please be specific about
what you recall and rate your confidence in each item.

Topics to rotate:
  A1. "TypeScript error handling in the SkillForge router"
  A2. "Adding new step types to the SkillForge Skill IR"
  A3. "My preferences for commit message style and PR structure"
```

**Scoring rubric (0–10 each):**

| Dimension | 0 | 5 | 10 |
|---|---|---|---|
| Relevance | Completely off-topic recalls | Mixed: some useful, some noise | All recalled items directly applicable |
| Specificity | Generic statements ("be careful") | File names or concepts named | Exact file:line, variable names, error messages |
| Confidence calibration | Over-confident on stale info | Some appropriate hedging | Explicitly distinguishes high vs low confidence |
| Novel signal | Nothing you didn't already know | 1–2 new useful reminders | 3+ non-obvious reminders you needed |

**Max score: 40 per prompt × 3 runs = 120 points per category.**

---

### Category B — Code Quality (tests review gate + learned patterns)

**What it measures:** Whether accumulated review gate patterns and project conventions are
being applied proactively.

**Prompt template (run 3×, each targeting a different code smell):**

```
Review this code snippet and identify any issues. Be specific about severity:

[Insert 20-line TypeScript snippet with 2–3 intentional issues]

Snippet set (rotate):
  B1. A function with a missing try/catch around an external API call (security.md violation)
  B2. A switch statement on a discriminated union missing a case (TypeScript exhaustiveness)
  B3. A React component with state mutation inside the render path (coding-style.md violation)
```

**Scoring rubric (0–10 each):**

| Dimension | 0 | 5 | 10 |
|---|---|---|---|
| Issue detection | Misses seeded issues | Finds 1 of 2 seeded issues | Finds all seeded issues |
| Rule citation | No reference to conventions | Generic "this is a problem" | Cites specific rule from coding-style/security/testing.md |
| Fix quality | No fix offered | Vague suggestion | Exact corrected code provided |
| False positive rate | 3+ non-issues flagged | 1 non-issue flagged | No false positives |

**Max score: 40 per prompt × 3 runs = 120 points per category.**

---

### Category C — Planning Coherence (tests recall + planner agent)

**What it measures:** Whether prior project decisions, architectural patterns, and constraints
are correctly incorporated into new plans without re-litigating settled decisions.

**Prompt template (run 3×, each on a different planning task):**

```
Plan the implementation of [FEATURE] for the SkillForge project. Include:
- Files to create/modify
- Which packages are affected
- Any non-negotiable constraints from existing architecture
- Estimated complexity

Features to plan:
  C1. "Add streaming support to the llm_call executor step"
  C2. "Add a new 'batch_llm_call' step type to the Skill IR"
  C3. "Add cost tracking per step to the ExecutionResult"
```

**Scoring rubric (0–10 each):**

| Dimension | 0 | 5 | 10 |
|---|---|---|---|
| CLAUDE.md adherence | Contradicts stated invariants | Acknowledges constraints passingly | All SEAMS_ONLY / additive-only constraints respected |
| Cross-package awareness | Treats packages as independent | Notes 1–2 inter-package concerns | Full dependency graph: skill-ir → runtime → executor |
| Constraint recall | No project-specific constraints cited | 1 constraint cited | 3+ constraints cited with rationale |
| Plan accuracy | Missing key files | Correct general direction | Exact files named with correct paths |

**Max score: 40 per prompt × 3 runs = 120 points per category.**

---

### Category D — Session Continuity (tests DepthFusion memory persistence)

**What it measures:** Whether context from prior sessions (not just this conversation) is
accessible and correctly weighted.

**Prompt template — ask in a fresh session (no prior context in window):**

```
Without looking at any files, tell me:
1. What is the DepthFusion integration status with SkillForge as of today?
2. What were the 3 main TypeScript errors encountered during SF-4 implementation?
3. What is Greg's preferred commit message format?
4. What is the BUDGET_FRACTIONS key that caused a mismatch during Seam E implementation?

Rate your confidence (high/medium/low) for each answer.
```

**Scoring rubric (0–10 each question):**

| Score | Criteria |
|---|---|
| 0 | No recall, fabricated answer |
| 3 | Vague partial recall ("something about types") |
| 7 | Correct but incomplete (right topic, missing detail) |
| 10 | Accurate, specific, with correct confidence calibration |

**Correct answers (for evaluation):**
1. SF-1 through SF-5 complete; MCP server connected; weighted_retrieval + recursive_llm_call step types added
2. TS2366 (validator.ts missing cases), TS2339 (schema.ts missing next field), executor stub wrong StepResult shape
3. `type(scope): description` conventional commits; imperative mood; body explains WHY
4. `documents` (not `tools` as the integration plan mistakenly assumed)

**Max score: 40 points per session × 3 sessions = 120 points per category.**

---

### Category E — Tool Suggestion Quality (tests skill registry + context awareness)

**What it measures:** Whether Claude proactively suggests the right skill/tool/agent for a
given task without being prompted.

**Prompt template (state a task, don't ask which tool):**

```
I need to [TASK]. How should I approach this?

Tasks to use:
  E1. "run a thorough review of the SkillForge executor before committing"
  E2. "plan the next sprint for SkillForge"
  E3. "debug a TypeScript error I'm seeing in the router"
  E4. "figure out what I was doing with the DepthFusion integration last week"
```

**Scoring rubric (0–10 each):**

| Dimension | 0 | 5 | 10 |
|---|---|---|---|
| Correct skill invoked | Wrong or no skill suggested | Partially right skill | Exact correct skill invoked proactively |
| Rationale quality | No explanation | Generic explanation | Cites the skill's registered `use_when` condition |
| Prerequisite awareness | Jumps straight to action | Notes 1 prerequisite | All prerequisites surfaced (e.g. "run /recall first") |

**Max score: 30 per prompt × 4 tasks × 3 runs = 360 points per category (normalise to 120).**

---

## 3. Enhancement Registry

Track each enhancement separately so you can compare individual contributions.

| Enhancement | Type | Date Applied | Baseline Score | Post Score | Delta | Notes |
|---|---|---|---|---|---|---|
| DepthFusion MCP (C1-C11) | MCP server | 2026-03-28 | — | — | — | Baseline not yet captured |
| SkillForge fusion layer (SF-1–SF-5) | TypeScript package | 2026-03-28 | — | — | — | |

---

## 4. Running a Benchmark

### Step 1 — Capture baseline (before the enhancement is applied)

```bash
# On the VPS, before applying the enhancement
cd ~/Development/Projects/depthfusion
python3 -m depthfusion.metrics.collector --tag "baseline-$(date +%Y%m%d)" \
  --event "benchmark_start" 2>/dev/null || echo "collector not yet active"
```

Until the collector is automated, **record scores manually** in the Enhancement Registry table.

### Step 2 — Run the benchmark battery

For each category (A–E), run each prompt 3 times in a fresh Claude Code session. Score each
run against the rubric. Record the raw scores.

### Step 3 — Apply the enhancement

```bash
# Example: adding a new skill
cp new-skill.md ~/.claude/skills/
claude mcp restart depthfusion 2>/dev/null || true
```

### Step 4 — Re-run the benchmark battery

Same prompts, same fresh sessions, same scoring rubric.

### Step 5 — Compute delta

```
delta = (post_score - baseline_score) / baseline_score × 100%
```

A delta > 5% in any category is considered a **meaningful improvement**.
A delta < -5% in any category is a **regression** — investigate before shipping.

---

## 5. Composite Score Formula

Combine all 5 categories into a single **Claude Instance Quality Score (CIQS)**:

```
CIQS = (A×0.25) + (B×0.20) + (C×0.20) + (D×0.25) + (E×0.10)

Where each category score is normalised to [0, 100].
```

**Weighting rationale:**
- A (Retrieval) and D (Session continuity): 25% each — DepthFusion's core contribution is memory
- B (Code quality) and C (Planning): 20% each — skills/rules contribution
- E (Tool suggestion): 10% — registry/meta-awareness, hardest to isolate

---

## 6. Recommended Measurement Cadence

| When | What to measure |
|---|---|
| Before any new enhancement | Full battery (A–E) as baseline snapshot |
| After each new MCP server | Category A (retrieval) + Category D (continuity) |
| After each new skill/plugin | Category B (code quality) + Category E (tool suggestion) |
| After each new hook | Category C (planning) |
| Monthly | Full battery to track compound drift |

---

## 7. Sharing Scores with Others

When recommending enhancements to other users, report:

```
Enhancement: [name]
Type: MCP / skill / hook / plugin
CIQS delta: +X% overall
Biggest win: Category [X]: +Y%
Biggest risk: None / Category [X]: -Y% (acceptable tradeoff)
Tested on: [Claude version], [OS], [date]
Reproducibility: [did you run it 3× per prompt? yes/no]
```

This gives other users enough signal to decide whether to apply the same enhancement to their
own instance, with appropriate context about what it does and doesn't improve.

---

## 8. Baseline Run — Action Required

**Before your next enhancement, run the full battery and record scores here:**

| Category | Run 1 | Run 2 | Run 3 | Average | Date |
|---|---|---|---|---|---|
| A — Retrieval Quality | — | — | — | — | |
| B — Code Quality | — | — | — | — | |
| C — Planning Coherence | — | — | — | — | |
| D — Session Continuity | — | — | — | — | |
| E — Tool Suggestion | — | — | — | — | |
| **CIQS** | | | | **—** | |

This baseline will be your reference point for all subsequent measurements.
