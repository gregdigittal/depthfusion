# Negative-Signal Gold Set

> **Target:** S-48 AC-2 false-negative rate ≤ 0.10 on 40 labelled examples

## Contents

- `_seeds/` — illustrative examples pinning the schema (committed with S-64 scaffolding; excluded from eval by default)
- `NNN-{slug}.json` — production examples (target: 40)

## What counts as a negative signal

A **negative signal** is a statement in a session that tells us what **didn't work** or **should be avoided**. Typical forms:

- "X failed because Y"
- "Don't use X when Z"
- "X was the wrong choice — we should have used Y"
- "Avoid X; it breaks under condition Z"

A **positive signal** is the opposite — what worked, what to do. Decisions are a positive-like signal (captured by the decision extractor, not the negative extractor).

## Schema (`negative/v1`)

```json
{
  "schema": "negative/v1",
  "source_session": "2026-04-15-my-session",
  "input_text": "... session content ...",
  "expected_type": "negative",
  "expected_negatives": [
    {"what": "concrete thing that failed or was rejected", "why": "mechanism or reason"}
  ],
  "reviewer_notes": "borderline calls; why each was or wasn't a negative",
  "labelled_by": "greg",
  "labelled_at": "2026-04-21"
}
```

### `expected_type`

- `negative`: the input contains at least one genuine negative signal; `expected_negatives` lists them
- `positive`: the input contains *no* negative signals; `expected_negatives: []`

### Edge cases to cover

| Edge case | Purpose |
|---|---|
| Frustration phrased without a concrete mechanism ("this is awful") | Should **not** be flagged — too vague |
| Concrete failure with resolution ("X timed out; we added a retry") | Negative is the timeout cause, not the fix |
| Irony / hypothetical ("if X failed, we'd do Y") | Should not be flagged |
| Negative + positive in the same paragraph | Tests extractor boundary detection |
| Multiple distinct negatives in one session | Tests per-item extraction |

## Running the eval

```bash
python scripts/eval_negative.py
python scripts/eval_negative.py --include-seeds
```
