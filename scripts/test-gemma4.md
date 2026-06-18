# Test Gemma 4

## Direct inference test

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/gemma-4-26b-a4b-it-4bit","messages":[{"role":"user","content":"In one sentence, what is DepthFusion?"}],"max_tokens":80}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
```

## Check which model is loaded

```bash
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
```

## Health check

```bash
curl -s http://127.0.0.1:8000/health
```
