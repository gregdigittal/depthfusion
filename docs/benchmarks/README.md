# CIQS Benchmark Methodology

> **Owner:** E-26 S-63 (Benchmark Harness & Evaluation Data)
> **Source prompt doc:** [`docs/performance-measurement-prompt.md`](../performance-measurement-prompt.md)
> **Battery (machine-readable):** [`prompts/ciqs-battery.yaml`](prompts/ciqs-battery.yaml)
> **Scripts:** [`scripts/ciqs_harness.py`](../../scripts/ciqs_harness.py), [`scripts/ciqs_summarise.py`](../../scripts/ciqs_summarise.py)

This directory holds CIQS benchmark inputs, outputs, and summary reports. The measurement protocol is automated where possible and explicit where it can't be (scoring).

---

## What CIQS is (and isn't)

CIQS stands for **Claude Instance Quality Score** — a 5-category rubric-scored measurement of how well a given Claude Code installation responds to a battery of prompts. It's a relative measure, not an absolute one: the point is to detect **deltas** between configurations (before/after enabling DepthFusion, before/after a new skill, local vs vps-cpu vs vps-gpu).

It is **not**:
- A unit test (we have `tests/test_benchmark/test_ciqs_proxy.py` for the fast synthetic inner loop)
- A benchmark of Claude's raw ability (it measures the *installation*, which includes DepthFusion, skills, hooks, MCP servers)
- A statistical significance test on a single run (3+ runs are required; bootstrap CI is the headline number)

---

## The five categories

| ID | Name | What it measures | Retrieval-only? |
|---|---|---|---|
| **A** | Retrieval Quality | MCP retrieval + RRF + AttnRes weighting | ✅ Yes (harness auto-executes) |
| **B** | Code Quality | Review gate patterns + learned conventions | ❌ Requires full Claude Code response |
| **C** | Planning Coherence | Recall + planner agent composition | ❌ Requires full response |
| **D** | Session Continuity | DepthFusion memory persistence across sessions | ❌ Requires full response |
| **E** | Tool Suggestion Quality | Skill registry + context awareness | ❌ Requires full response |

"Retrieval-only" categories can be executed end-to-end by the harness: it calls the DepthFusion recall tool and captures retrieved blocks. Non-retrieval categories need a human or judge-model to run the prompt through Claude Code in a fresh session and score the response.

---

## The three-stage flow

```
                  [1] run                      [2] score                [3] summarise
                  ─────                        ─────────                ──────────────
                                      ┌──────────────────────┐
 ┌────────────┐  ciqs_harness.py run  │ -raw.jsonl           │                        ┌─────────────┐
 │ battery    │ ───────────────────►  │ -scoring.md (template)├───────┐                │ -summary.md │
 │ .yaml      │                       └──────────────────────┘       │                │ (bootstrap  │
 └────────────┘                                                       │                │  CIs,       │
                                                    operator fills in │                │  per-cat    │
                                                                      ▼                │  stats)     │
                                                       ┌──────────────────────┐       └─────────────┘
                                       ciqs_harness.py │ -scoring.md (filled) │             ▲
                                          score        └──────────┬───────────┘             │
                                       ────────────────►          │                         │
                                                                  ▼                         │
                                                       ┌──────────────────────┐             │
                                                       │ -scored.jsonl        │             │
                                                       └──────────┬───────────┘             │
                                                                  │  ciqs_summarise.py      │
                                                                  └─────────────────────────┘
```

### Stage 1: `run`

```bash
python scripts/ciqs_harness.py run \
    --battery docs/benchmarks/prompts/ciqs-battery.yaml \
    --mode local --run 1
```

Produces two files under `docs/benchmarks/`:
- `YYYY-MM-DD-{mode}-run{N}-raw.jsonl` — one line per prompt, with retrieved blocks for Category A
- `YYYY-MM-DD-{mode}-run{N}-scoring.md` — human-fillable scoring template

### Stage 2: `score`

The operator opens the `-scoring.md` template and fills in integer scores (0–10) for each rubric dimension. For retrieval-only categories, the blocks are inlined in the template. For others, the operator runs the prompt through Claude Code in a fresh session and scores the response against the rubric.

```bash
python scripts/ciqs_harness.py score \
    --raw docs/benchmarks/2026-04-21-local-run1-raw.jsonl \
    --scoring docs/benchmarks/2026-04-21-local-run1-scoring.md
```

Produces `2026-04-21-local-run1-scored.jsonl`.

### Stage 3: `summarise`

After 3 runs (minimum) are scored:

```bash
python scripts/ciqs_summarise.py \
    --mode local \
    docs/benchmarks/2026-04-21-local-run1-scored.jsonl \
    docs/benchmarks/2026-04-22-local-run2-scored.jsonl \
    docs/benchmarks/2026-04-23-local-run3-scored.jsonl \
    --out docs/benchmarks/2026-04-23-local-summary.md
```

Produces a markdown report with per-category mean, stddev, and bootstrap 95% CI, plus raw normalised scores for inspection.

---

## Conventions

### Filenames

- `YYYY-MM-DD-{mode}-run{N}-raw.jsonl` — stage 1 output
- `YYYY-MM-DD-{mode}-run{N}-scoring.md` — stage 1 output (human-fillable)
- `YYYY-MM-DD-{mode}-run{N}-scored.jsonl` — stage 2 output
- `YYYY-MM-DD-{mode}-summary.md` — stage 3 output (final artefact, committed)
- `{version}-baseline.md` — legacy per-release gate report (pre-S-63 format; see `v0.5.0-baseline.md`)

### What to commit

| File | Commit? | Why |
|---|---|---|
| `-raw.jsonl` | ✅ Yes | Reproducibility — lets a reviewer check the prompt context |
| `-scoring.md` (filled) | ✅ Yes | The scores themselves are provenance |
| `-scored.jsonl` | ✅ Yes | Source of truth for the summariser |
| `-summary.md` | ✅ Yes | The final artefact linked from releases / BACKLOG |

### Modes

`local`, `vps-cpu`, `vps-gpu` are the three installer modes. The `--mode` flag is a label — it does **not** switch DepthFusion's mode. Set `DEPTHFUSION_MODE` in your environment before running the harness to actually exercise that path.

---

## Minimum for a reportable result

- **3 runs minimum per mode** — stochasticity in LLM responses means a single run can swing by 15+ points per category
- **All 17 topics scored** — a missing topic invalidates the category average
- **Fresh sessions for B/C/D/E** — each prompt must be run in a fresh Claude Code session to avoid context contamination. Category A (retrieval-only) is deterministic enough to not need this
- **Bootstrap CI width reported** — a narrow CI (< 5 pts) means results are stable; a wide CI (> 15 pts) means you need more runs

---

## What the math does

`scripts/ciqs_summarise.py` computes:

- **Normalised score per topic:** `sum(rubric dims) / (10 * num_dims) * 100`
- **Per-category stats:** mean and stddev of normalised topic scores (across all topics × runs for that category)
- **95% bootstrap CI for the mean:** 5000 resamples, 2.5/97.5 percentile. Bootstrap is chosen because with 3–5 runs we cannot assume normality and the classical t-CI would be too narrow.

The seed is fixed (1729) so the same input JSONL always produces the same CI. Change it if you want independent resampling.

---

## Limits of this harness

- **Not fully end-to-end automated.** The scoring step is operator/judge-model, not pure code. A future `--judge=haiku` flag in `ciqs_harness.py score` would auto-score against the rubric using a model — deferred to v2.
- **No per-block source attribution in retrieval.** The harness captures what DepthFusion returned, not which backend served each block. See the dogfood runbook (`docs/runbooks/dogfood-telemetry.md`) for how to cross-reference with the recall JSONL stream.
- **GPU mode is untested.** No `vps-gpu` baseline exists; that's gated on the VPS migration (E-19).
- **Proxy vs live CIQS.** `tests/test_benchmark/test_ciqs_proxy.py` runs in seconds and is the regression gate; the live harness here takes hours (with the 3× fresh-session requirement) and is used for release-milestone measurement, not per-commit.

---

## Existing artefacts

- [`v0.5.0-baseline.md`](v0.5.0-baseline.md) — pre-S-63 proxy-only baseline. Not a full live CIQS run; use only as a regression floor, not as a v0.5 quality claim.
