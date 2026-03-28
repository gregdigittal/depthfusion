# DepthFusion

Depth-aware memory fusion for Claude Code — weighted block retrieval, session attention, context routing, and recursive LLM integration.

## Overview

DepthFusion enhances Claude Code with:

- **Weighted block retrieval** — AttnRes-inspired cosine similarity over session block embeddings → softmax attention weights → RRF × block_weight × source_weight ([arXiv:2603.15031](https://arxiv.org/abs/2603.15031))
- **Session tagging** — automatic `.meta.yaml` sidecars for session files (never modifies `.tmp` files — C1 compliant)
- **Context bus** — project-isolated pub/sub with InMemoryBus and FileBus backends
- **Recursive reasoning** — `rlm` integration with cost ceiling enforcement
- **MCP server** — 5 tools exposed to Claude Code via stdio

## Architecture

```
src/depthfusion/
├── core/        — types, config, scoring (softmax/cosine/weighted), feedback (JSONL + source weights)
├── fusion/      — rrf (k=60), weighted (AttnRes), block_retrieval (k-means), reranker
├── session/     — tagger (.meta.yaml), scorer (tag+keyword), loader (top-k), compactor
├── router/      — bus (InMemory/File), publisher, subscriber, dispatcher, cost_estimator
├── recursive/   — trajectory, sandbox (restricted subprocess), strategies (4 presets), client (rlm)
├── analyzer/    — scanner (~/.claude inventory), compatibility (C1-C11), recommender, installer
├── mcp/         — server (5 tools gated by feature flags)
└── metrics/     — collector (JSONL daily rotation), aggregator + digest formatter
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
# Register MCP server with Claude Code (user scope)
claude mcp add depthfusion --scope user -- $(pwd)/.venv/bin/python -m depthfusion.mcp.server
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `depthfusion_status` | Feature flag states and module health |
| `depthfusion_recall_relevant` | Semantic session block retrieval |
| `depthfusion_tag_session` | Tag a session file → writes `.meta.yaml` sidecar |
| `depthfusion_publish_context` | Publish a ContextItem to the bus |
| `depthfusion_run_recursive` | Run a recursive reasoning strategy via rlm |

## Feature Flags

All components are independently gated (all default `true`):

| Env Var | Controls |
|---------|---------|
| `DEPTHFUSION_FUSION_ENABLED` | Weighted fusion path in dispatcher |
| `DEPTHFUSION_SESSION_ENABLED` | Session tagging in hooks |
| `DEPTHFUSION_RLM_ENABLED` | rlm recursive reasoning |
| `DEPTHFUSION_ROUTER_ENABLED` | Context bus pub/sub |
| `DEPTHFUSION_METRICS_ENABLED` | JSONL metrics collection |

## C1-C11 Compatibility

DepthFusion respects 11 compatibility constraints protecting the existing Claude Code infrastructure:

```bash
python -m depthfusion.analyzer.compatibility
```

Results: 10 GREEN · 1 YELLOW (C4 — CLaRa indicator in postcss node_modules, benign)

## Development

```bash
source .venv/bin/activate
pytest                    # 286 tests, all GREEN
pytest --cov=depthfusion  # 85.98% coverage
mypy src/                 # clean
ruff check src/ tests/    # clean
```

## Dependencies

- Python ≥ 3.10
- `numpy` ≥ 1.24
- `pyyaml` ≥ 6.0
- `structlog` ≥ 24.0
- `rlm` (optional) — install from `~/Development/Projects/rlm/` for recursive LLM support
