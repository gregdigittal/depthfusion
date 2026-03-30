# DepthFusion

Cross-session memory for Claude Code — tiered retrieval architecture with BM25, haiku semantic reranking, and ChromaDB vector storage.

## What It Actually Does (v0.3.0)

Measured improvement over vanilla Claude Code (CIQS benchmark, 2026-03-28):

| Version | CIQS Overall | Category A (retrieval) | Category D (continuity) | Notes |
|---------|-------------|----------------------|------------------------|-------|
| Vanilla CC | ~76.5 | — | — | Baseline |
| v0.1.0 | ~80 | keyword matching | — | — |
| v0.2.0 | ~83–85 | BM25 + block chunking | 42% | Manual /learn required |
| v0.3.0 local | ~85 | BM25 (unchanged) | 42% | Zero new deps |
| v0.3.0 VPS Tier 1 | ~88 (projected) | BM25 + haiku reranker | ≥65% | Auto-capture via PostCompact hook |
| v0.3.0 VPS Tier 2 | ~90 (projected) | BM25 + ChromaDB + haiku | ≥70% | Requires 500+ sessions |

VPS Tier 1 and Tier 2 projections require `ANTHROPIC_API_KEY` and a large session corpus respectively. Local mode scores are measured against the live session corpus (71 blocks, 5 test queries — all returned correct top-1 results).

**Category D ceiling (all modes):** DepthFusion retrieves only what has been written to `~/.claude/`. In local mode, facts must be written manually via `/learn`. VPS mode eliminates this gap with auto-capture via the PostCompact hook.

---

## Architecture

```
Install mode: DEPTHFUSION_MODE=local|vps

Local mode:
  query → BM25 (top-k) → results

VPS Tier 1 (< 500 sessions):
  query → BM25 (top-10) → HaikuReranker → top-k

VPS Tier 2 (≥ 500 sessions):
  query → ChromaDB (top-20) + BM25 (top-10) → RRF fusion → HaikuReranker → top-k

Auto-capture (VPS only):
  PreCompact hook  → snapshot active state to ~/.claude/.depthfusion-compact-snapshot.json
  PostCompact hook → haiku summarization → ~/.claude/shared/discoveries/{date}-autocapture.md
```

```
src/depthfusion/
├── core/        — types, config, scoring, feedback
├── fusion/      — rrf (k=60), weighted, block_retrieval, reranker
├── session/     — tagger (.meta.yaml), scorer, loader, compactor
├── router/      — bus (InMemory/File), publisher, subscriber, dispatcher
├── recursive/   — trajectory, sandbox, strategies, client (rlm)
├── analyzer/    — scanner, compatibility (C1-C11), recommender, installer
├── mcp/         — server (8 tools gated by feature flags)
├── retrieval/   — bm25.py, reranker.py (haiku), hybrid.py (RRF pipeline)
├── capture/     — auto_learn.py (heuristic), compressor.py (haiku)
├── storage/     — vector_store.py (ChromaDB), tier_manager.py
└── install/     — install.py (CLI), migrate.py (Tier 1 → Tier 2)
```

---

## Install

### Local mode (zero external dependencies)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m depthfusion.install.install --mode local
claude mcp add depthfusion --scope user -- $(pwd)/.venv/bin/python -m depthfusion.mcp.server
export DEPTHFUSION_MODE=local
```

**Limitations:** No semantic reranking. Category D continuity requires manual `/learn` after each session.

### VPS mode (haiku reranker + ChromaDB Tier 2)

> ⚠️ **Billing warning:** Do NOT set `ANTHROPIC_API_KEY` in `~/.claude/settings.json` or your shell
> environment. Claude Code reads this variable as its own auth credential and will switch your entire
> billing from your Pro/Max subscription to pay-per-token API billing for **all** Claude Code usage —
> not just DepthFusion. Use `DEPTHFUSION_API_KEY` instead (see below).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[vps-tier2]"
export DEPTHFUSION_MODE=vps
python -m depthfusion.install.install --mode vps
claude mcp add depthfusion --scope user -- $(pwd)/.venv/bin/python -m depthfusion.mcp.server
```

To enable Haiku summarization (optional — heuristic extraction works without it):

```bash
# In ~/.claude/depthfusion.env (NOT in settings.json env block):
DEPTHFUSION_HAIKU_ENABLED=true
DEPTHFUSION_API_KEY=sk-ant-your-key-here
```

**Tier promotion:** When your corpus crosses 500 sessions (configurable via `DEPTHFUSION_TIER_THRESHOLD`), run the migration script to activate ChromaDB vector retrieval:

```bash
python -m depthfusion.install.migrate         # index everything into ChromaDB
python -m depthfusion.install.migrate --dry-run  # preview without writing
```

---

## MCP Tools

| Tool | Description | Mode |
|------|-------------|------|
| `depthfusion_status` | Feature flag states and module health | All |
| `depthfusion_recall_relevant` | Tier-aware session block retrieval | All |
| `depthfusion_tag_session` | Tag a session file → writes `.meta.yaml` sidecar | All |
| `depthfusion_publish_context` | Publish a ContextItem to the context bus | All (router_enabled) |
| `depthfusion_run_recursive` | Run a recursive reasoning strategy via rlm | All (rlm_enabled) |
| `depthfusion_tier_status` | Corpus size, active tier, sessions until promotion | All |
| `depthfusion_auto_learn` | Trigger auto-extraction from recent .tmp session files | All |
| `depthfusion_compress_session` | Compress a specific .tmp file into a discovery file | All |

---

## Feature Flags

| Env Var | Controls | Default |
|---------|---------|---------|
| `DEPTHFUSION_MODE` | `local` or `vps` install mode | `local` |
| `DEPTHFUSION_TIER_THRESHOLD` | Session count threshold for Tier 2 promotion | `500` |
| `DEPTHFUSION_TIER_AUTOPROMOTE` | Auto-promote to Tier 2 when corpus crosses threshold | `true` (VPS) |
| `DEPTHFUSION_FUSION_ENABLED` | Weighted fusion path in dispatcher | `true` |
| `DEPTHFUSION_SESSION_ENABLED` | Session tagging in hooks | `true` |
| `DEPTHFUSION_RLM_ENABLED` | rlm recursive reasoning | `true` |
| `DEPTHFUSION_ROUTER_ENABLED` | Context bus pub/sub | `true` |
| `DEPTHFUSION_METRICS_ENABLED` | JSONL metrics collection | `true` |
| `DEPTHFUSION_HAIKU_ENABLED` | Haiku API calls for summarization/extraction (opt-in) | `false` |
| `DEPTHFUSION_API_KEY` | API key for Haiku features — use instead of `ANTHROPIC_API_KEY` | — |

---

## C1-C11 Compatibility

DepthFusion respects 11 compatibility constraints protecting the existing Claude Code infrastructure:

```bash
python -m depthfusion.analyzer.compatibility
```

Results: 10 GREEN · 1 YELLOW (C4 — CLaRa indicator in postcss node_modules, benign)

---

## Development

```bash
source .venv/bin/activate
pytest                    # 328 tests, all GREEN (2 skipped — chromadb not installed)
pytest --cov=depthfusion  # coverage report
mypy src/                 # clean
ruff check src/ tests/    # clean
```

---

## Dependencies

- Python ≥ 3.10
- `numpy` ≥ 1.24
- `pyyaml` ≥ 6.0
- `structlog` ≥ 24.0
- `anthropic` ≥ 0.40 (optional — required for VPS mode haiku reranker/summarizer)
- `chromadb` ≥ 0.4 (optional — required for VPS Tier 2; install with `pip install -e ".[vps-tier2]"`)
- `rlm` (optional — install from `~/Development/Projects/rlm/` for recursive LLM support)
