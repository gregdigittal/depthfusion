# DepthFusion Evaluation Sets

> **Owner:** E-26 S-64 (Labelled Evaluation Data)
> **Consumed by:** `scripts/eval_decision.py`, `scripts/eval_dedup.py`, `scripts/eval_negative.py`
> **Purpose:** Labelled gold sets that turn capture-mechanism precision/recall claims from "asserted" into "measured".

This directory holds three gold sets corresponding to three capture mechanisms shipped in v0.5:

| Gold set | Consumed by | What it measures | Target AC |
|---|---|---|---|
| `decision-extraction/` | `eval_decision.py` | Precision of `capture/decision_extractor.py` on a labelled session corpus | S-45 AC-1 ≥ 0.80 |
| `dedup/` | `eval_dedup.py` | False-positive rate of `capture/dedup.py` on labelled near-duplicate pairs | S-49 AC-2 ≤ 0.05 |
| `negative/` | `eval_negative.py` | False-negative rate of `capture/negative_extractor.py` on labelled negative-signal examples | S-48 AC-2 ≤ 0.10 |

---

## Labelling protocol

### Seed vs production

Each set has a `_seeds/` subdirectory holding 2–3 illustrative examples committed as part of S-64 scaffolding. These are **not** eval data — they exist to pin the JSON schema and smoke-test the eval scripts. Use `--include-seeds` to include them in a run; otherwise they are excluded.

Production examples live directly under the set's directory (not in `_seeds/`). The target sizes per the S-64 acceptance criteria:

- `decision-extraction/` — **50 sessions** labelled
- `dedup/` — **30 near-duplicate pairs** labelled
- `negative/` — **40 examples** labelled

Short of those targets, eval reports are suggestive, not reportable.

### Labelling workflow

1. Pick a real example (a session file for decisions, a discovery pair for dedup, a session snippet for negatives).
2. Anonymise if needed — replace personal names, keep technical content. Do not ship PII or credentials.
3. Save under `{set}/{NNN}-{short-slug}.json` using the schema below.
4. Annotate your judgement in the `label` / `expected` field. Be explicit about borderline calls in `reviewer_notes`.
5. Commit with a message pattern: `data(eval): {set} — add example {NNN} ({slug})`.

### Inter-rater agreement

For the first round of labelling, two reviewers should independently label the same 10 examples per set. If Cohen's kappa > 0.70 across a set, proceed with single-rater labelling for the rest. If < 0.70, the labelling guide needs tightening — pause and fix the rubric before more labour is invested.

### Adding a new example

```bash
# 1. Pick a session file or discovery pair
cp ~/.claude/sessions/2026-04-15-my-session.jsonl docs/eval-sets/decision-extraction/003-refactor-decision.json

# 2. Rewrite to the schema (see per-set README)
$EDITOR docs/eval-sets/decision-extraction/003-refactor-decision.json

# 3. Verify the eval script accepts it
python scripts/eval_decision.py --single docs/eval-sets/decision-extraction/003-refactor-decision.json

# 4. Commit
git add docs/eval-sets/decision-extraction/003-refactor-decision.json
git commit -m "data(eval): decision-extraction — add example 003 (refactor-decision)"
```

---

## What the eval scripts do

Each `eval_*.py` script:

1. Globs the set directory for `*.json` (excluding `_seeds/` unless `--include-seeds`)
2. Loads each file, validates schema
3. Runs the corresponding **heuristic** extractor or scorer against the input (not the LLM variant — see below)
4. Compares output to the `expected` field
5. Reports: counts (TP/FP/FN/TN), precision, recall, F1, and per-example diagnostics for misclassifications

### Why heuristic only (for now)

The heuristic variants are deterministic, fast, and free. Running the LLM variants against a 50-example set takes N API calls per run, which is costly during iterative label-building. Heuristic precision/recall is a lower-bound; once the gold sets stabilise, a `--extractor=llm` flag can add the higher-quality pass (see T-202 follow-up).

---

## Per-set schemas

### `decision-extraction/{NNN}-{slug}.json`

```json
{
  "schema": "decision-extraction/v1",
  "source_session": "2026-04-15-my-session",
  "input_text": "... full session content (or representative excerpt) ...",
  "expected": [
    {
      "text": "Migrated auth module from class-based to function-based middleware because middleware composition was breaking on async handlers",
      "category": "architecture"
    },
    {
      "text": "Rejected tRPC in favour of typed REST because the team owns more REST tooling",
      "category": "stack-choice"
    }
  ],
  "reviewer_notes": "Excluded 'added unit tests' — not a decision, it's standard work",
  "labelled_by": "greg",
  "labelled_at": "2026-04-21"
}
```

**Precision:** `|TP| / |TP + FP|` where TP = extracted decisions that match an `expected` entry (loose text similarity — cosine ≥ 0.80 on bag-of-words), FP = extracted but no match.

### `dedup/{NNN}-{slug}.json`

```json
{
  "schema": "dedup/v1",
  "label": "true-dup" | "false-dup",
  "a": "... first discovery content ...",
  "b": "... second discovery content ...",
  "reviewer_notes": "Both describe the same observation about BM25 length normalisation",
  "labelled_by": "greg",
  "labelled_at": "2026-04-21"
}
```

**False-dedup rate:** `|false-dup pairs flagged as duplicate| / |false-dup pairs|`. The dedup code uses cosine sim ≥ 0.92; the eval script counts how often that threshold triggers on `false-dup` pairs.

### `negative/{NNN}-{slug}.json`

```json
{
  "schema": "negative/v1",
  "source_session": "2026-04-15-my-session",
  "input_text": "... session content ...",
  "expected_type": "negative" | "positive",
  "expected_negatives": [
    {
      "what": "Haiku reranker with top-N=100 inputs",
      "why": "rate-limit errors at 8k concurrent sessions"
    }
  ],
  "reviewer_notes": "Borderline — 'timed out' flagged as negative but could be intermittent",
  "labelled_by": "greg",
  "labelled_at": "2026-04-21"
}
```

**False-negative rate:** `|genuine negatives missed| / |genuine negatives|`. The eval script runs `HeuristicNegativeExtractor` on `input_text` and checks coverage of `expected_negatives`.

---

## Committing results

The eval scripts write their reports to `docs/benchmarks/eval-reports/{YYYY-MM-DD}-{set}.md`. Commit those alongside any gold-set changes so the progression is auditable.

---

## Sensitivity notes

- **Gold-set bias.** A single reviewer labelling all examples embeds their judgement. At 50 examples, that's the headline number, not a ground truth. Document the reviewer in each file.
- **Synthetic vs real.** Prefer real sessions. If a synthetic example is needed to cover a specific edge case (e.g., "decision phrased as question"), tag it with `"synthetic": true` in the JSON so the eval script can count synthetics separately.
- **Negative examples.** Every set benefits from "expected no output" examples (no decision, clearly not a dup, purely positive content). Aim for ~20% negatives.
