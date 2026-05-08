# DepthFusion

Cross-session memory for Claude Code — tiered retrieval (BM25 → semantic rerank → vector fusion), structured capture mechanisms, and a memory-policy layer that lets you pin, score, and provide feedback on what gets recalled.

> **Status:** v0.6.0a2 (alpha, post-E-29 polish, 2026-05-08). 1430 tests across 42 closed user stories. The MCP surface (18 tools) is stable; observability is comprehensive but live-corpus benchmarks for the GPU mode are calendar-blocked.

---

## Performance Impact

DepthFusion is benchmarked against vanilla Claude Code with the **CIQS** (Claude Code Information-retrieval Quality Score) suite. Categories: **A** = retrieval precision, **D** = continuity (cross-session memory). Higher is better; vanilla Claude Code baseline is ~76.5.

### Measured (live data)

| Mode | CIQS Overall | Cat A | Cat D | Recall p95 | Notes |
|---|---|---|---|---|---|
| Vanilla CC | 76.5 | — | — | n/a | Baseline |
| v0.2.0 local | ~83–85 | BM25 + block chunking | 42% | <50 ms | Manual `/learn` required |
| v0.3.0 local | ~85 | BM25 (unchanged) | 42% | <50 ms | Zero new deps |
| v0.6.0a1+ recall (real sessions, 2026-05-07) | not benchmarked | not benchmarked | not benchmarked | **37–372 ms** (n=4 events) | Production-path validated post-S-79 |

The 37–372 ms range was captured during S-79 AC-2 validation across 4 real recall events on the live host (mode=vps, result_count=3, two sessions). Full p95 measurement requires the dogfood follow-up (S-79 AC-4 — needs ≥ 5 days of accumulated emissions).

### Estimated (projection — pending live benchmarks)

| Mode | CIQS Overall (proj.) | Cat A delta | Cat D | Recall p95 budget | Confidence |
|---|---|---|---|---|---|
| v0.3.0 vps-cpu (Tier 1: BM25 + Haiku rerank) | ~88 | +2 vs local | ≥65% | ≤ 1500 ms | Medium — projected from S-43/S-44 ACs |
| v0.3.0 vps-cpu (Tier 2: + ChromaDB fusion) | ~90 | +3 vs local | ≥70% | ≤ 1500 ms | Medium — requires 500+ session corpus |
| v0.6.0a2 vps-gpu (Gemma + local embeddings) | not yet projected | +3 vs Tier 1 | not yet projected | ≤ 1500 ms | **Low confidence — live GPU benchmark blocked on E-26 harness completion** |

The vps-gpu projections are explicitly **estimates** until E-26 (Benchmark Harness) ships its 50-decision / 30-dedup / 40-negative gold sets and the harness re-runs against a real GPU host.

### What this session's polish (E-29) changed

E-29 (v0.5.3 polish, this session: S-79–S-84) was **observability work, not retrieval work**. There is no expected change to retrieval quality or latency. What you get:

- **Per-capability latency** in every recall event (was: only reranker timed)
- **`config_version_id`** populated on every event (was: empty in 100% of dogfood events)
- **`backend_fallback_chain`** per-query cascade trace (was: empty in 100% of dogfood events)
- **`system.startup`** event on MCP init (was: silent — empty metrics dir was indistinguishable from "server never ran")
- **Test/prod telemetry separation** so `~/.claude/depthfusion-metrics/` reflects only real usage

These together unblock S-43 AC-3 (p95 latency per capability), S-64 AC-2 (GPU migration phase 4 latency table), and the next dogfood pass.

---

## Architecture

```
Install mode: DEPTHFUSION_MODE=local | vps-cpu | vps-gpu

local mode (laptop, zero API cost):
  query → BM25 (top-k) → results

vps-cpu mode (cloud VPS, default):
  query → BM25 (top-10) → Haiku reranker (API) → top-k
  Tier 2 (when corpus ≥ 500 sessions):
    query → ChromaDB (top-20) + BM25 (top-10) → RRF fusion → Haiku reranker → top-k

vps-gpu mode (CUDA host, on-box Gemma via vLLM):
  query → BM25 (top-10) + local embeddings → RRF fusion → Gemma reranker → top-k
  LLM capabilities (extract / summarise / link / decision_extractor)
  route to on-box Gemma; embeddings route to local sentence-transformers.
  Haiku fallback via FallbackChain when DEPTHFUSION_API_KEY is set.

Auto-capture (vps-cpu / vps-gpu):
  PreCompact hook  → snapshot active state
  PostCompact hook → Haiku/Gemma summarisation → ~/.claude/shared/discoveries/{date}-autocapture.md
  SessionStart hook → auto-recall of relevant past discoveries

Memory-policy layer (E-27, v0.6.0a1):
  pin_discovery       → exempt high-value entries from age-based pruning
  set_memory_score    → operator override of importance / salience scalars
  recall_feedback     → bounded salience deltas based on used vs ignored chunks
```

```
src/depthfusion/
├── core/        — types, config, scoring, feedback (RecallStore, FeedbackResult)
├── fusion/      — rrf (k=60), weighted, block_retrieval, reranker, gates (Mamba B/C/Δ)
├── session/     — tagger (.meta.yaml), scorer, loader, compactor
├── router/      — bus (InMemory/File), publisher, subscriber, dispatcher
├── recursive/   — trajectory, sandbox, strategies, client (rlm)
├── analyzer/    — scanner, compatibility (C1-C11), recommender, installer, prune
├── mcp/         — server (18 tools gated by feature flags)
├── retrieval/   — bm25, reranker (haiku/gemma), hybrid (RRF pipeline), embedding
├── capture/     — auto_learn, compressor, decision_extractor, negative_extractor,
│                  confirm_discovery, dedup, event_hook (high-importance signal)
├── storage/     — vector_store (ChromaDB), tier_manager, decay
├── metrics/     — collector (4 streams), aggregator (backend_summary, capture_summary)
├── backends/    — factory (quality-ranked chains), chain (FallbackChain), null,
│                  haiku, gemma, local_embedding
└── install/     — install (CLI), migrate (Tier 1 → Tier 2)
```

---

## Install

DepthFusion has three install modes. Pick the one matching your target:

| Mode | Use when | LLM backend | Extras | Step-by-step |
|---|---|---|---|---|
| `local` | Laptop, zero API deps | Heuristics + BM25 only | `[local]` | inline below |
| `vps-cpu` | Cloud VPS, no GPU | Haiku via API | `[vps-cpu]` | **[docs/install/vps-cpu-quickstart.md](docs/install/vps-cpu-quickstart.md)** |
| `vps-gpu` | CUDA host (≥ 20 GB VRAM) | Local Gemma via vLLM | `[vps-gpu]` | **[docs/install/vps-gpu-quickstart.md](docs/install/vps-gpu-quickstart.md)** |

The two quickstart guides are the canonical, fully-tested install procedures for non-local hosts. Follow them; the inline `local` snippet is a 2-minute laptop install only.

> ⚠️ **Billing safety — always use `DEPTHFUSION_API_KEY`, never `ANTHROPIC_API_KEY`.**
> Claude Code reads `ANTHROPIC_API_KEY` as its own auth credential and will switch your **entire** Claude Code billing from your Pro/Max subscription to pay-per-token API for everything — not just DepthFusion. The separate `DEPTHFUSION_API_KEY` exists specifically to prevent this. The installer refuses to use `ANTHROPIC_API_KEY`.

### Prerequisites

- Python ≥ 3.10 (3.10–3.13 supported; Ubuntu 24.04 ships 3.12 by default)
- `pip` ≥ 23.0
- `git` (to clone)
- A working `~/.claude/` directory (Claude Code installed and run at least once)
- For vps-cpu: `ANTHROPIC_API_KEY` available to set as `DEPTHFUSION_API_KEY` (separate variable)
- For vps-gpu: CUDA 12.x + ≥ 20 GB VRAM + `nvidia-smi` on PATH

### Clone & install

```bash
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion

# Pin to a known-good SHA. Latest verified: 2f6b212 (2026-05-08, post-E-29 polish).
# Tagged release: v0.6.0a2 (when cut — currently published from main).
git checkout 2f6b212    # OR: git checkout v0.6.0a2  (once tag exists)

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### Quick install — `local` mode (laptop, zero API cost)

```bash
pip install -e '.[local]'
python3 -m depthfusion.install.install --mode=local

# Register with Claude Code (user scope = available in every project)
claude mcp add depthfusion --scope user -- python3 -m depthfusion.mcp.server

# Verify
claude mcp list | grep depthfusion         # should show "✓ Connected"
ls ~/.claude/depthfusion-metrics/          # should exist after first MCP call
```

**Limitations of `local` mode:** No semantic reranking (BM25 only). Cat-D continuity requires manual `/learn` after each session. No auto-capture (no PostCompact hook).

### Install — `vps-cpu` mode (recommended for cloud servers)

Follow **[docs/install/vps-cpu-quickstart.md](docs/install/vps-cpu-quickstart.md)** end-to-end (~10 min). It covers:

1. Python venv setup with PEP 668 fallback for Ubuntu 24.04
2. `pip install -e '.[vps-cpu]'` (pulls in `anthropic`, `chromadb`)
3. `python3 -m depthfusion.install.install --mode=vps-cpu --api-key="$DEPTHFUSION_API_KEY"`
4. MCP server registration in `~/.claude.json` (user-level, NOT `~/.claude/settings.json`)
5. SessionStart, PreCompact, PostCompact hook installation in `~/.claude-shared/hooks/`
6. Weekly regression-monitor systemd timer (CIQS comparison every Sunday)
7. Verification: trigger a real session, confirm `~/.claude/depthfusion-metrics/<today>-recall.jsonl` populates

### Install — `vps-gpu` mode (CUDA host)

Follow **[docs/install/vps-gpu-quickstart.md](docs/install/vps-gpu-quickstart.md)** end-to-end (~4 hrs, mostly Gemma model download). It covers:

1. CUDA + driver verification (`nvidia-smi`)
2. `pip install -e '.[vps-gpu]'` (pulls in `sentence-transformers`, plus `vllm` separately)
3. vLLM systemd service for local Gemma inference (root-level)
4. `python3 -m depthfusion.install.install --mode=vps-gpu`
5. Local embedding model download (sentence-transformers cache)
6. Hook installation as in vps-cpu
7. Verification: `nvidia-smi` shows VRAM use during recall; `depthfusion_describe_capabilities` shows `gemma` as the active reranker

### Tier promotion (vps-cpu / vps-gpu only)

When your corpus crosses 500 sessions (configurable via `DEPTHFUSION_TIER_THRESHOLD`), promote to Tier 2:

```bash
python3 -m depthfusion.install.migrate --dry-run    # preview the index plan
python3 -m depthfusion.install.migrate              # index everything into ChromaDB
```

Tier promotion is one-way and idempotent. Re-running on an already-indexed corpus is safe.

### Post-install verification

After any install, run this 60-second smoke test from a fresh Claude Code session:

```bash
# In Claude Code, ask anything — recall should fire on session start
# Then in your terminal:
ls -lh ~/.claude/depthfusion-metrics/$(date -u +%Y-%m-%d)*.jsonl
# Expected: at least one of *.jsonl, -recall.jsonl, -capture.jsonl exists with non-zero size
```

If the metrics directory is empty after a real session, check the hook chain:

```bash
ls -la ~/.claude-shared/hooks/depthfusion-*.sh   # should be executable
grep PYTHON ~/.claude-shared/hooks/depthfusion-session-init.sh   # should match your venv path
```

(Empty metrics + working `claude mcp list` is the **substrate gap** signature — see `docs/runbooks/dogfood-telemetry.md` §6.)

---

## Full Feature Set (v0.6.0a2)

### Retrieval pipeline

- **BM25 lexical retrieval** — block-level chunking, k1=1.5, b=0.75 defaults, project-scoped IDF
- **Haiku semantic reranker** (vps-cpu) — top-10 → top-k via Anthropic API
- **Gemma reranker** (vps-gpu) — same role, local inference via vLLM
- **Local embeddings** (vps-gpu) — sentence-transformers, cosine similarity
- **ChromaDB vector store** (Tier 2, 500+ sessions) — HNSW ANN over embeddings
- **RRF fusion** (k=60) — combines BM25 and vector results when both engaged
- **Selective fusion gates** (S-51, Mamba B/C/Δ) — α-blended source weighting; opt-in via `DEPTHFUSION_FUSION_GATES_ENABLED=true`
- **Quality-ranked fallback chains** (S-44 / DR-018 §4) — Gemma → Haiku → Null with typed-error rerouting
- **Cross-project / project-scoped recall** — defaults to current project; `cross_project=true` searches all
- **Trajectory-depth telemetry** (S-32 AC-3) — recency × source reliability blending

### Capture mechanisms (CMs)

| CM | Source | What it captures |
|---|---|---|
| CM-1 | `decision_extractor` | Architectural / API / config decisions from session text |
| CM-2 | `dedup` | Near-duplicate filtering at write time |
| CM-3 | git post-commit hook | Commit messages → discoveries with `type: commit` |
| CM-4 | PRECEDED_BY graph edges | Temporal cross-session links (S-50) |
| CM-5 | `confirm_discovery` MCP tool | Operator-confirmed facts (active capture) |
| CM-6 | `negative_extractor` | "Avoid X" / "don't do Y" signals |

PostCompact and PreCompact hooks chain CMs with auto-summarisation; SessionStart auto-recall surfaces discoveries at session boot.

### Memory policy layer (E-27)

- **`pin_discovery`** (S-69) — exempt high-value discoveries from age-based pruning
- **`importance` and `salience` scalars** (S-70) — separate intrinsic value from recent usefulness; defaults derived from extractor confidence; operator override via `set_memory_score`
- **Bucketed decay** (S-71) — high-importance entries persist longer
- **`recall_feedback`** (S-72) — bounded salience deltas (`+0.1` per used, `-0.05` per ignored), idempotent by `(recall_id, chunk_id)`
- **High-importance event hook** (S-73) — structured event when a discovery is published with `importance ≥ 0.8`
- **Idempotent `publish_context`** (S-78) — exact-content dedup by `content_hash`

### Knowledge graph (E-11, v0.4.0)

- Entity extraction during capture
- `graph_traverse` MCP tool — walk from a named entity via typed edges
- `graph_status` — node/edge counts, coverage, tier
- `set_scope` — `project` / `cross_project` / `global` traversal scope
- PRECEDED_BY edges (S-50) for temporal continuity

### Observability (E-22, E-24, E-29)

Four daily JSONL streams under `~/.claude/depthfusion-metrics/`:

| Stream | What's in it |
|---|---|
| `YYYY-MM-DD.jsonl` | Simple metrics (counter increments, fallback events, `system.startup`) |
| `YYYY-MM-DD-recall.jsonl` | Per-query recall: `backend_used`, `backend_fallback_chain` (per-query trace), `latency_ms_per_capability` (all 6 capabilities), `total_latency_ms`, `result_count`, `config_version_id`, `event_subtype` |
| `YYYY-MM-DD-capture.jsonl` | Per-write capture: `capture_mechanism`, `file_path`, `chars_written`, `event_subtype`, `config_version_id` |
| `YYYY-MM-DD-gates.jsonl` | Mamba B/C/Δ gate audit (opt-in via `DEPTHFUSION_FUSION_GATES_ENABLED=true`) |

Aggregation tools: `MetricsAggregator.backend_summary()` (per-backend latency + error rates), `MetricsAggregator.capture_summary()` (per-mechanism write rates).

Runbook: **[docs/runbooks/dogfood-telemetry.md](docs/runbooks/dogfood-telemetry.md)** for the weekly self-audit protocol.

### Recursive LLM (rlm, E-04)

- `run_recursive` MCP tool — multi-step reasoning with sandbox isolation
- Trajectory tracking, retry strategies, cost ceiling enforcement
- Optional dependency: `rlm` package

### Compatibility constraints (C1–C11)

11 invariants protecting the existing Claude Code infrastructure (CLaRa, hook chain, `~/.claude/` layout). Run `python -m depthfusion.analyzer.compatibility` for a live check. Currently 10 GREEN · 1 YELLOW (benign).

---

## MCP Tools (18 total)

| Tool | Description | Required flag |
|---|---|---|
| `depthfusion_status` | Feature-flag states + module health | always |
| `depthfusion_recall_relevant` | Tier-aware session block retrieval | always |
| `depthfusion_tag_session` | Tag a session file → `.meta.yaml` sidecar | always |
| `depthfusion_publish_context` | Publish to context bus, idempotent by content_hash | `router_enabled` |
| `depthfusion_run_recursive` | Recursive reasoning via rlm | `rlm_enabled` |
| `depthfusion_tier_status` | Corpus size, active tier, sessions until promotion | always |
| `depthfusion_auto_learn` | Trigger extraction from recent `.tmp` session files | always |
| `depthfusion_compress_session` | Compress a `.tmp` file into a discovery | always |
| `depthfusion_graph_traverse` | Walk entity graph from a named entity | `graph_enabled` |
| `depthfusion_graph_status` | Graph health: nodes, edges, coverage | `graph_enabled` |
| `depthfusion_set_scope` | Traversal scope: project / cross_project / global | `graph_enabled` |
| `depthfusion_confirm_discovery` | Active capture (CM-5) | always |
| `depthfusion_prune_discoveries` | Archive stale discoveries (90+ days, unpinned) | always |
| `depthfusion_set_memory_score` | Override importance / salience (S-70) | always |
| `depthfusion_recall_feedback` | Apply bounded salience deltas from used/ignored chunks (S-72) | always |
| `depthfusion_pin_discovery` | Exempt a discovery from age-based pruning (S-69) | always |
| `depthfusion_describe_capabilities` | Which retrieval layers + CMs engage in this instance (S-76) | always |
| `depthfusion_inspect_discovery` | Parsed frontmatter of a discovery file (S-76) | always |

Full tool documentation with response shapes: see `docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md` §2.

---

## Feature Flags

| Env Var | Controls | Default |
|---|---|---|
| `DEPTHFUSION_MODE` | `local` / `vps-cpu` / `vps-gpu` | `local` |
| `DEPTHFUSION_API_KEY` | API key for Haiku features (use **instead of** `ANTHROPIC_API_KEY`) | — |
| `DEPTHFUSION_TIER_THRESHOLD` | Session count for Tier 2 promotion | `500` |
| `DEPTHFUSION_TIER_AUTOPROMOTE` | Auto-promote on threshold crossing | `true` (vps) |
| `DEPTHFUSION_FUSION_ENABLED` | Weighted fusion in dispatcher | `true` |
| `DEPTHFUSION_FUSION_GATES_ENABLED` | Mamba B/C/Δ selective fusion gates + audit stream | `false` |
| `DEPTHFUSION_SESSION_ENABLED` | Session tagging in hooks | `true` |
| `DEPTHFUSION_RLM_ENABLED` | Recursive LLM tool | `true` |
| `DEPTHFUSION_ROUTER_ENABLED` | Context bus pub/sub | `true` |
| `DEPTHFUSION_GRAPH_ENABLED` | Knowledge graph tools | `true` (vps) |
| `DEPTHFUSION_METRICS_ENABLED` | JSONL metrics collection | `true` |
| `DEPTHFUSION_HAIKU_ENABLED` | Haiku API for summarisation/extraction | `false` |
| `DEPTHFUSION_BACKEND_FALLBACK_LOG` | Emit fallback events to metrics stream | `true` |
| `DEPTHFUSION_PRUNE_AGE_DAYS` | Age threshold for `prune_discoveries` | `90` |
| `DEPTHFUSION_RLM_COST_CEILING` | Per-call rlm cost ceiling (USD) | `0.50` |
| `DEPTHFUSION_EMBEDDING_BACKEND` | `local` / `null` (vps-gpu only) | `local` (vps-gpu) |

---

## Compatibility check (C1–C11)

```bash
python -m depthfusion.analyzer.compatibility
```

Expected: **10 GREEN · 1 YELLOW** (C4 — CLaRa indicator string in PostCSS `node_modules`, benign).

---

## Development

```bash
source .venv/bin/activate

pytest                              # 1430 tests, GREEN (a few skipped if chromadb/cuda absent)
pytest tests/test_metrics/ -q       # 83 tests — observability
pytest tests/test_backends/ -q      # 225 tests — backend chain + factory
pytest --cov=depthfusion            # coverage report
mypy src/                           # clean
ruff check src/ tests/              # clean
```

The `tests/conftest.py` autouse fixture redirects bare `MetricsCollector()` calls to per-session tmp dirs during pytest runs (S-82). Production paths require explicit `metrics_dir=` if you want to write to `~/.claude/depthfusion-metrics/` from a test.

---

## Dependencies

- **Python** ≥ 3.10 (3.10–3.13 supported)
- `numpy` ≥ 1.24
- `pyyaml` ≥ 6.0
- `structlog` ≥ 24.0
- `anthropic` ≥ 0.40 — `[vps-cpu]` extra; required for Haiku reranker. Also pulled in by `[vps-gpu]` for the Haiku fallback.
- `chromadb` ≥ 0.4 — `[vps-cpu]` and `[vps-gpu]` extras; required for Tier 2 vector retrieval
- `sentence-transformers` ≥ 2.2 — `[vps-gpu]` extra; local embeddings
- `vllm` — `[vps-gpu]`-adjacent; installed separately via the vps-gpu quickstart (see runbook)
- `rlm` — optional; install from source for recursive LLM support

```bash
pip install -e '.[local]'     # Laptop, zero external deps
pip install -e '.[vps-cpu]'   # Cloud VPS, Haiku API
pip install -e '.[vps-gpu]'   # CUDA host, local Gemma + embeddings
```

The legacy `vps-tier1` / `vps-tier2` extras were removed in v0.6.0 (see S-56 / S-57). Use the three-mode extras above.

---

## Project status & roadmap

- **Closed:** 42 user stories across E-01 through E-28; E-27 (Memory Policy Layer) ready for downstream consumption (see `docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md`).
- **Active:** E-29 (v0.5.3 polish) — code-complete this session; calendar-gated ACs (S-79 AC-2/AC-4, S-80 AC-4) await ≥ 5 days of dogfood emissions.
- **Backlog:** E-26 (Benchmark Harness) — eval-set curation (50 decisions / 30 dedup pairs / 40 negatives) blocked on calendar + ≥ 7 days of real discovery content; E-16 S-35 (SkillForge HTTP sidecar) blocked on SkillForge SF-2.

See `BACKLOG.md` for the full ledger.
