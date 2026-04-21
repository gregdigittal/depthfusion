# Decision-Extraction Gold Set

> **Target:** S-45 AC-1 precision ≥ 0.80 on 50 labelled sessions

## Contents

- `_seeds/` — illustrative examples pinning the schema (committed with S-64 scaffolding; excluded from eval by default)
- `NNN-{slug}.json` — production examples (target: 50)

## Schema (`decision-extraction/v1`)

```json
{
  "schema": "decision-extraction/v1",
  "source_session": "2026-04-15-my-session",
  "input_text": "... full session content (or representative excerpt) ...",
  "expected": [
    {"text": "decision as authored in the session", "category": "architecture|stack-choice|refactor|policy|other"}
  ],
  "reviewer_notes": "any caveats, borderline calls, what was excluded",
  "labelled_by": "greg",
  "labelled_at": "2026-04-21"
}
```

### `expected` conventions

- List all decisions the extractor *should* identify. Omit trivial work items ("added tests", "fixed typo") — those are not decisions.
- Use the decision author's own phrasing when possible (paraphrase only if needed for anonymisation).
- Category is a soft label for diagnostics; the extractor doesn't currently categorise, so this is informational.

### Edge cases to cover

| Edge case | Purpose |
|---|---|
| Decision phrased as a question ("do we use tRPC?") with a resolution later in the same session | Tests multi-utterance aggregation |
| Multiple decisions in one session with varying confidence | Tests per-decision extraction |
| Session with no decisions (pure implementation work) | Negative example; `expected: []` |
| Decision later reversed within the same session | Tests final-state handling |
| Decision implicit in a diff description | Tests non-prose extraction |

## Running the eval

```bash
python scripts/eval_decision.py
# Or against a single file:
python scripts/eval_decision.py --single docs/eval-sets/decision-extraction/001-auth-migration.json
# Include _seeds/ too:
python scripts/eval_decision.py --include-seeds
```
