#!/bin/bash
# Print cost summary from ~/.claude/v2-cost.jsonl
LEDGER="${CLAUDE_COST_LEDGER:-$HOME/.claude/v2-cost.jsonl}"
if [[ ! -f "$LEDGER" ]]; then echo "No ledger at $LEDGER"; exit 0; fi
echo "=== V2 Cost Ledger Summary ==="
python3 -c "
import json, sys, collections
entries = [json.loads(l) for l in open('$LEDGER') if l.strip()]
by_model = collections.defaultdict(lambda: {'input': 0, 'output': 0, 'calls': 0})
for e in entries:
    m = e.get('model','unknown')
    by_model[m]['input'] += e.get('input_tokens', 0)
    by_model[m]['output'] += e.get('output_tokens', 0)
    by_model[m]['calls'] += 1
print(f'Total entries: {len(entries)}')
for m, s in sorted(by_model.items()):
    print(f'  {m}: {s[\"calls\"]} calls, {s[\"input\"]:,} in / {s[\"output\"]:,} out tokens')
"
