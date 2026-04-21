# Dedup Gold Set

> **Target:** S-49 AC-2 false-dedup rate ≤ 0.05 on 30 labelled near-duplicate pairs

## Contents

- `_seeds/` — illustrative examples pinning the schema (committed with S-64 scaffolding; excluded from eval by default)
- `NNN-{slug}.json` — production pairs (target: 30)

## Schema (`dedup/v1`)

```json
{
  "schema": "dedup/v1",
  "label": "true-dup",
  "a": "... first discovery content (markdown body, no frontmatter) ...",
  "b": "... second discovery content ...",
  "reviewer_notes": "why these are / aren't duplicates",
  "labelled_by": "greg",
  "labelled_at": "2026-04-21"
}
```

### `label` conventions

- `true-dup`: both discoveries describe the same observation or decision; the older should be superseded when the newer lands
- `false-dup`: topically related but genuinely different — keeping both is correct

### Target distribution

Aim for **roughly 50/50** true-dup to false-dup split. Under-sampling false-dup means the false-dedup-rate number is noisy.

### Edge cases to cover

| Edge case | Purpose |
|---|---|
| Same fact, one sentence vs. paragraph | Tests length-independence of cosine sim |
| Same topic, opposite conclusions ("X works" vs. "X doesn't work") | Should be false-dup — tests semantic similarity gotchas |
| Near-identical wording but different project context | Should be false-dup — tests project-scoping |
| Paraphrased decision with different terminology | Should be true-dup |

## Running the eval

```bash
python scripts/eval_dedup.py
python scripts/eval_dedup.py --include-seeds
python scripts/eval_dedup.py --threshold 0.90  # explore threshold sensitivity
```
