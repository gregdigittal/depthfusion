# Systemd User Services

## depthfusion-rest.service

REST API server (port 7300). Requires `depthfusion.env` at `~/.claude/depthfusion.env`.

**Install:**
```bash
cp depthfusion-rest.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now depthfusion-rest
```

**Required env vars in depthfusion.env:**
- `DEPTHFUSION_HNSW_ENABLED=true` — enables HNSW vector index
- `DEPTHFUSION_VECTOR_SEARCH_ENABLED=true` — enables vector search in recall
- `DEPTHFUSION_EMBEDDING_BACKEND=local` — use local sentence-transformers backend
- `DEPTHFUSION_MODE=vps-gpu` — enables the local embedding backend chain

## depthfusion-mcp.service

MCP HTTP/SSE server (port 7301). Already present — see existing service file.
