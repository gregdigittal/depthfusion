# Dogfood Telemetry Runbook

> **Epic/Story:** E-26 S-65 — *dogfood-telemetry runbook*
> **Status:** Active — runbook landed 2026-04-21; first pass pending
> **Purpose:** Validate the v0.5.1 / v0.5.2 observability layer by using DepthFusion on real work for ≥ 1 week, then inspecting the structured streams to find fields that are empty-when-they-shouldn't-be, fields that lie, and fields we wish we'd instrumented.

This is **not** a benchmark. CIQS measurement lives in `scripts/ciqs_harness.py` and uses labelled prompts. This runbook is the opposite direction: unlabelled real usage, looking for instrumentation gaps the unit tests didn't catch.

---

## 1. Mental model

v0.5.1 and v0.5.2 shipped four daily JSONL streams under `~/.claude/depthfusion-metrics/`:

| File | Written by | What's in it |
|---|---|---|
| `YYYY-MM-DD.jsonl` | `MetricsCollector.record()` | Simple `(metric, value, labels)` tuples (v0.3 legacy stream) |
| `YYYY-MM-DD-gates.jsonl` | `MetricsCollector.record_gate_log()` | Mamba B/C/Δ fusion-gate audit entries, one per `apply_fusion_gates()` call |
| `YYYY-MM-DD-recall.jsonl` | `MetricsCollector.record_recall_query()` | Per-query backend routing, per-capability latency, fallback chain |
| `YYYY-MM-DD-capture.jsonl` | `MetricsCollector.record_capture_event()` | Decision / negative / dedup / git-hook / confirm writes |

Two aggregators read these streams:

- `MetricsAggregator.backend_summary(target_date)` → per `capability::backend` latency + error-rate table
- `MetricsAggregator.capture_summary(target_date)` → per capture-mechanism success/supersede counts

Everything is fail-open — emission errors never break serving.

---

## 2. Prereqs (one-time)

```bash
# 1. Verify DepthFusion is installed in the Python env Claude Code uses
python -c "import depthfusion; print(depthfusion.__version__)"
# Expected: 0.5.2 (or later)

# 2. Verify the MCP server is reachable from Claude Code
# (settings.json → mcpServers → depthfusion should be configured)

# 3. Verify the metrics directory exists and is writable
ls -la ~/.claude/depthfusion-metrics/ 2>/dev/null || mkdir -p ~/.claude/depthfusion-metrics
```

**No env flags need setting.** All four streams emit by default as of v0.5.2. The earlier dual-gate pattern (S-60 integration) is already on.

---

## 3. Daily usage protocol

**Do nothing special.** Use Claude Code normally for a week. The instrumentation rides along.

Sessions to favour during the observation window:

- [ ] At least one `/goal`-style multi-task session (produces many recall calls + captures at the end)
- [ ] At least one session that invokes the git post-commit hook (CM-3)
- [ ] At least one session that finishes with decisions captured (CM-1)
- [ ] At least one session where recall returns "no hits" (tests the zero-path)
- [ ] At least one session that uses `cross_project=true` (exercises the S-52 code path)

Don't manufacture fake work. Authentic usage is what we're trying to measure.

---

## 4. End-of-week aggregation

### 4a. Quick visual sanity check

```bash
cd ~/.claude/depthfusion-metrics
ls -la | tail -20
# Expect: 4 files per active day (no missing-stream gaps)

wc -l *-recall.jsonl | tail -10
wc -l *-capture.jsonl | tail -10
# Rough eyeball: are per-day counts reasonable given how much you used DepthFusion?
```

### 4b. Per-day backend summary

```bash
python -c "
from datetime import date, timedelta
from depthfusion.metrics.collector import MetricsCollector
from depthfusion.metrics.aggregator import MetricsAggregator
import json

agg = MetricsAggregator(MetricsCollector())
for delta in range(7, 0, -1):
    d = date.today() - timedelta(days=delta)
    s = agg.backend_summary(d)
    if s.get('per_backend'):
        print(f'=== {d} ===')
        print(json.dumps(s, indent=2, default=str))
"
```

### 4c. Per-day capture summary

```bash
python -c "
from datetime import date, timedelta
from depthfusion.metrics.collector import MetricsCollector
from depthfusion.metrics.aggregator import MetricsAggregator
import json

agg = MetricsAggregator(MetricsCollector())
for delta in range(7, 0, -1):
    d = date.today() - timedelta(days=delta)
    s = agg.capture_summary(d)
    if s.get('per_mechanism'):
        print(f'=== {d} ===')
        print(json.dumps(s, indent=2, default=str))
"
```

### 4d. Raw-stream spot checks

```bash
# Latency outliers (slowest 10 recall queries in the last week)
cat ~/.claude/depthfusion-metrics/*-recall.jsonl \
  | jq -r 'select(.total_ms != null) | [.total_ms, .mode, .event_subtype, .query] | @tsv' \
  | sort -rn | head -10

# Error-mode distribution
cat ~/.claude/depthfusion-metrics/*-recall.jsonl \
  | jq -r '.event_subtype' | sort | uniq -c | sort -rn

# Fallback chain occurrences (which backends actually got used?)
cat ~/.claude/depthfusion-metrics/*-recall.jsonl \
  | jq -r '.backends_invoked[]?' | sort | uniq -c | sort -rn

# Capture-mechanism distribution
cat ~/.claude/depthfusion-metrics/*-capture.jsonl \
  | jq -r '.capture_mechanism' | sort | uniq -c | sort -rn
```

---

## 5. Analysis checklist (what to look for)

Copy this table into your dogfood report and fill it in.

### 5a. "Absence vs zero" — do the streams even exist?

| Stream | File present? | Non-empty? | Fields populated? | Notes |
|---|---|---|---|---|
| `YYYY-MM-DD.jsonl` | ☐ | ☐ | ☐ | Legacy v0.3 stream — mostly cold paths |
| `YYYY-MM-DD-gates.jsonl` | ☐ | ☐ | ☐ | Should emit when `DEPTHFUSION_FUSION_GATES_ENABLED` is on |
| `YYYY-MM-DD-recall.jsonl` | ☐ | ☐ | ☐ | **Must** have entries if you used recall at all |
| `YYYY-MM-DD-capture.jsonl` | ☐ | ☐ | ☐ | Needs at least one session-end capture |

A missing stream for a feature you used is a finding. Document it.

### 5b. Field-level sanity on `recall` entries

Pick 5 random entries from `*-recall.jsonl` and check:

| Field | Expected | Failure mode to watch for |
|---|---|---|
| `event_subtype` | `"ok" \| "error" \| "timeout" \| ...` | Unknown values silently coerced to `"ok"` — grep the DEBUG log for `"unknown event_subtype"` to catch callers that drifted |
| `mode` | `"local" \| "vps-cpu" \| "vps-gpu"` | Stale or unset → mode detection regressed |
| `backends_invoked` | List of concrete backend names | Empty list → `_detect_current_backends()` didn't fire; all-nulls → backends reported as unhealthy |
| `perf_ms` | Dict of per-phase times | Missing keys for phases that definitely ran → latency threading broke |
| `latency_ms_per_capability` | `{"reranker": N, "linker": N, ...}` | All-zero for an active capability → probable S-61 follow-through gap |
| `fallback_chain` | List describing primary → fallback flow | Empty on an error-path entry → fallback logic not being logged |
| `config_version_id` | sha256[:12] or empty-sentinel | Empty everywhere means gate config tracking didn't wire to recall path (that's fine for non-gate queries) |

### 5c. Field-level sanity on `capture` entries

| Field | Expected | Failure mode |
|---|---|---|
| `capture_mechanism` | One of the 5 known values | Typo coercion — the validator rejects unknowns; a silently-wrong caller would land here |
| `project` | Slug from `detect_project()` or `"global"` | Always `"global"` → project detection not finding the git remote / env var |
| `entries_written` | Integer count | Always 0 → dedup hot path never finds novel content (suspicious) |
| `write_success` | Boolean | Always `True` → no errors ever recorded → either system is perfect (unlikely) or errors don't reach this field |
| `superseded_count` | Integer (dedup only) | Always 0 on dedup events → dedup isn't actually matching similar discoveries |

### 5d. Cross-stream consistency

- For each day, does `backend_summary()`'s `per_backend` count roughly match the number of MCP recall tool invocations you remember?
- Do `capture_summary()` per-mechanism counts match git history (e.g. commits in the week should approximate `git_post_commit` count if the hook is installed)?

---

## 6. Triage — turn findings into tickets

For each finding, pick a category:

| Category | Example | Action |
|---|---|---|
| **Empty-field-when-shouldn't** | `backends_invoked: []` on a query that clearly used Haiku | New v0.5.3 story: "instrument $X in $location" |
| **Lying field** | `event_subtype: "ok"` on an entry with stack trace in `error_message` | v0.5.3 bug fix: classification logic wrong |
| **Wish-we-had-it** | Can't tell from `recall` entries which tier served which block | New v0.5.3 or v0.6 story: add `per_block_source` field |
| **False alarm** | Field looks wrong but code inspection shows expected behaviour | Document in runbook so next dogfood pass doesn't re-flag |
| **Instrumentation cost** | A stream is 10× larger than expected, slowing aggregation | v0.5.3 polish: add sampling or roll-up |

Create the v0.5.3 epic if findings warrant (likely). Reference this dogfood report by date in the story so the provenance is traceable.

---

## 7. Report template

Committed to `docs/runbooks/dogfood-reports/{YYYY-MM-DD}-week1.md`:

```markdown
# Dogfood Telemetry — Week 1

> Observation window: YYYY-MM-DD to YYYY-MM-DD
> Sessions: N (types: /goal x N, ad-hoc x N, ...)
> DepthFusion version at start: v0.5.2
> DepthFusion version at end: v0.5.2 (unchanged) or v0.5.x

## Stream health

[Fill in table from §5a]

## Recall-stream findings

[Field-level notes from §5b]

## Capture-stream findings

[Field-level notes from §5c]

## Cross-stream consistency

[Notes from §5d]

## Proposed v0.5.3 tickets

- [ ] S-6X: ...
- [ ] S-6X: ...

## What surprised me

[Free-form observations. The most valuable section. Anything that made you say "huh" goes here.]

## What to change in the runbook

[Self-corrections for the next pass.]
```

---

## 8. Known limits of this runbook

- **One operator, one week** is a small sample. Findings are suggestive, not statistical.
- **The dogfooder is the instrumenter.** Confirmation bias is real. A second person running this runbook on the same instance would catch different things.
- **Aggregator latency** on files > 100 MB is untested. If you generate that much data in a week, the aggregator calls in §4b/c will be slow — note it, don't wait.
- **This runbook does not exercise `vps-gpu`.** That path is gated on the migration. A `vps-gpu` dogfood pass is a separate deliverable under E-19.

---

## 9. Definition of Done for the first pass (AC-2)

- [ ] ≥ 7 days of real usage captured in `~/.claude/depthfusion-metrics/`
- [ ] Aggregator outputs from §4b and §4c run without error
- [ ] All four checklist sections (§5a..§5d) filled in
- [ ] Report committed at `docs/runbooks/dogfood-reports/{YYYY-MM-DD}-week1.md`
- [ ] Either a v0.5.3 epic opened OR a documented decision that findings are below polish threshold
