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
Install mode: DEPTHFUSION_MODE=local | vps-cpu | vps-gpu

local mode (laptop, zero API cost):
  query → BM25 (top-k) → results

vps-cpu mode (current default for cloud VPSes):
  query → BM25 (top-10) → Haiku reranker (API) → top-k
  Tier 2 (when corpus ≥ 500 sessions):
    query → ChromaDB (top-20) + BM25 (top-10) → RRF fusion → Haiku reranker → top-k

vps-gpu mode (CUDA host, local Gemma via vLLM):
  query → BM25 (top-10) + local embeddings → RRF fusion → Gemma reranker → top-k
  LLM capabilities (extract / summarise / link / decision_extractor)
  route to on-box Gemma; embeddings route to local sentence-transformers.
  Haiku fallback available via FallbackChain (v0.6.0a1) when DEPTHFUSION_API_KEY is set.

Auto-capture (vps-cpu / vps-gpu):
  PreCompact hook  → snapshot active state to ~/.claude/.depthfusion-compact-snapshot.json
  PostCompact hook → Haiku/Gemma summarisation → ~/.claude/shared/discoveries/{date}-autocapture.md
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

DepthFusion has three install modes. Pick the one that matches your target:

| Mode | Use when | LLM backend | Extras | Guide |
|---|---|---|---|---|
| `local` | Laptop, zero deps beyond Python | Heuristics + BM25 | none | see below |
| `vps-cpu` | Cloud VPS, no GPU, API-backed LLM | Haiku via API | `[vps-cpu]` | **[docs/install/vps-cpu-quickstart.md](docs/install/vps-cpu-quickstart.md)** |
| `vps-gpu` | CUDA host (≥ 20 GB VRAM) | Local Gemma via vLLM | `[vps-gpu]` | **[docs/install/vps-gpu-quickstart.md](docs/install/vps-gpu-quickstart.md)** |

The two quickstart guides are the canonical, step-by-step install
procedures. They cover virtualenv setup, PEP 668 pitfalls, venv
auto-activation, API-key handling, MCP registration, and the weekly
regression-monitor timer. Follow them for any vps-cpu or vps-gpu
deployment.

> ⚠️ **Billing safety — always use `DEPTHFUSION_API_KEY`, never `ANTHROPIC_API_KEY`.**
> Claude Code reads `ANTHROPIC_API_KEY` as its own auth credential and
> will switch your entire billing from your Pro/Max subscription to
> pay-per-token API for **all** Claude Code usage — not just DepthFusion.
> The separate `DEPTHFUSION_API_KEY` exists specifically to prevent
> this. The installer explicitly refuses to use `ANTHROPIC_API_KEY`.

### Quick install for `local` mode (laptop, zero API cost)

```bash
# Create a venv. On Ubuntu 24.04 you may need: sudo apt install -y python3-venv
python3 -m venv ~/venvs/depthfusion
source ~/venvs/depthfusion/bin/activate

# Install
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion
pip install --upgrade pip
pip install -e '.[local]'

# Configure
python3 -m depthfusion.install.install --mode=local

# Register with Claude Code
claude mcp add depthfusion --scope user -- python3 -m depthfusion.mcp.server
```

**Limitations of `local` mode:** No semantic reranking (BM25 only).
Category D continuity requires manual `/learn` after each session.

### Quickstart for `vps-cpu` and `vps-gpu`

For a new VPS or GPU host, don't copy the local-mode snippet above —
those environments have additional concerns (`python3-full` /
`python3-venv` install on fresh Ubuntu, `chromadb` compile
dependencies, vLLM systemd service, weekly timer setup, MCP
registration quirks). The two quickstart guides cover these
end-to-end:

- **[docs/install/vps-cpu-quickstart.md](docs/install/vps-cpu-quickstart.md)** — ~10 min, any CPU-only Linux host
- **[docs/install/vps-gpu-quickstart.md](docs/install/vps-gpu-quickstart.md)** — ~4 hrs, CUDA host with ≥ 20 GB VRAM
- **[docs/install/README.md](docs/install/README.md)** — decision overview: which guide to pick, when to run both (parallel-comparison plan)

### Tier promotion

When your corpus crosses 500 sessions (configurable via
`DEPTHFUSION_TIER_THRESHOLD`), run the migration script to activate
ChromaDB vector retrieval:

```bash
python3 -m depthfusion.install.migrate         # index everything into ChromaDB
python3 -m depthfusion.install.migrate --dry-run  # preview without writing
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
| `DEPTHFUSION_MODE` | `local`, `vps-cpu`, or `vps-gpu` install mode | `local` |
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

- Python ≥ 3.10 (any modern Python works — 3.10, 3.11, 3.12, 3.13. Ubuntu 24.04 ships 3.12 by default.)
- `numpy` ≥ 1.24
- `pyyaml` ≥ 6.0
- `structlog` ≥ 24.0
- `anthropic` ≥ 0.40 (optional — required for `vps-cpu` mode Haiku reranker and the Haiku fallback in `vps-gpu`)
- `chromadb` ≥ 0.4 (optional — required for Tier 2 vector retrieval; pulled in by both `[vps-cpu]` and `[vps-gpu]` extras)
- `sentence-transformers` ≥ 2.2 (optional — required for `vps-gpu` local embeddings; pulled in by `[vps-gpu]` extras)
- `vllm` (optional — required for `vps-gpu` local Gemma; installed separately via the vps-gpu quickstart)
- `rlm` (optional — install from `~/Development/Projects/rlm/` for recursive LLM support)

**Pick the right extras for your target host:**

```bash
pip install -e '.[local]'     # Laptop, zero external deps
pip install -e '.[vps-cpu]'   # Cloud VPS, Haiku API reranker
pip install -e '.[vps-gpu]'   # CUDA host, local Gemma + embeddings
```

The legacy `vps-tier1` / `vps-tier2` extras are deprecated in v0.5
and will be removed in v0.6 (see S-56 / S-57). Use the three-mode
extras above.
