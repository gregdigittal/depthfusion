#!/usr/bin/env bash
set -euo pipefail

BASE="http://127.0.0.1:8000"

echo "=== Health ==="
curl -s "$BASE/health"
echo

echo "=== Model loaded ==="
curl -s "$BASE/v1/models" | python3 -m json.tool
echo

echo "=== Inference test ==="
RESPONSE=$(curl -s "$BASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/gemma-4-26b-a4b-it-4bit","messages":[{"role":"user","content":"In one sentence, what is DepthFusion?"}],"max_tokens":512}')
echo "$RESPONSE" | python3 -m json.tool
echo
echo "=== Answer ==="
echo "$RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'choices' in d:
    print(d['choices'][0]['message']['content'])
else:
    print('ERROR:', d)
"
echo
