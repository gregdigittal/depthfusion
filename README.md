# DepthFusion

**DepthFusion gives AI agents the institutional memory your team already has.**

Every agent session starts from zero — it doesn't know what previous sessions discovered or what your teammates' agents figured out. DepthFusion fixes this: a shared memory layer where agents publish discoveries, new sessions inherit them instantly via `fabric_seed`, and every memory is queryable by provenance ("who knew what, when").

Built on Claude Code's MCP surface: tiered retrieval (BM25 → semantic rerank → vector fusion), structured capture, a cognitive infrastructure layer, and the Event Graph Fabric for multi-agent shared memory.

**[→ Animated demo](https://gregdigittal.github.io/depthfusion/depthfusion-animated-demo.html)**

> **Status:** v1.2.0 (2026-05-23). 2000+ tests passing · 0 ruff · 0 mypy. Event Graph Fabric (E-46) live — multi-agent shared memory, `fabric_seed` warm-start, agent provenance graph. MCP surface: 32 tools. SkillForge SF-2 + Mamba B/C/Δ + HNSW vector layer active.

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

### Estimated (projection)

| Mode | CIQS Overall (proj.) | Cat A delta | Cat D | Recall p95 budget | Confidence |
|---|---|---|---|---|---|
| v0.3.0 vps-cpu (Tier 1: BM25 + Haiku rerank) | ~88 | +2 vs local | ≥65% | ≤1500 ms | Medium |
| v0.3.0 vps-cpu (Tier 2: + ChromaDB fusion) | ~90 | +3 vs local | ≥70% | ≤1500 ms | Medium |
| **v1.0.0 (E-31 cognitive layer)** | **~94–96** | +4 vs Tier 2 | **≥85%** | **≤1500 ms** | Medium-high |

**v1.0.0 improvements over v0.6.0a2:**
- **Category D continuity: 70% → 85%+** — cognitive layer enables decision-aware recall; the system knows why it recalled something, not just that it matched
- **Contradiction prevention** — ContradictionEngine catches conflicting advice across sessions; estimated 40% reduction in contradictory guidance delivered to Claude
- **Token efficiency** — cognitive pre-filtering reduces irrelevant context surfaced per session; estimated 15–25% reduction in context tokens consumed at scale
- **8-component scoring overhead** — CognitiveScorer adds ~15 ms vs RRF-only; well within the 1500 ms p95 budget

---

## Architecture

```
Install mode: DEPTHFUSION_MODE=local | vps-cpu | vps-gpu | mac-mlx

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

mac-mlx mode (Apple Silicon, unified memory):
  query → BM25 (top-10) + local embeddings → RRF fusion → Gemma/Qwen reranker → top-k
  Uses mlx_lm.server (OpenAI-compatible) on port 8000.
  Same quality chain as vps-gpu; haiku fallback when mlx_lm.server is not running.
  Model options: gemma-3-12b (~7 GB), Qwen2.5-14B (~9 GB), Qwen2.5-32B (~20 GB).

Auto-capture (vps-cpu / vps-gpu):
  PreCompact hook  → snapshot active state
  PostCompact hook → Haiku/Gemma summarisation → ~/.claude/shared/discoveries/{date}-autocapture.md
  SessionStart hook → auto-recall of relevant past discoveries

Memory-policy layer (E-27, v0.6.0a1):
  pin_discovery       → exempt high-value entries from age-based pruning
  set_memory_score    → operator override of importance / salience scalars
  recall_feedback     → bounded salience deltas based on used vs ignored chunks

E-31 Cognitive Infrastructure Layer (v1.0.0, all enabled by default):
  query → [existing BM25/vector/RRF pipeline]
       → CognitiveScorer (8-component):
           semantic 0.25 | lexical 0.18 | confidence 0.15 | regime 0.12
           graph 0.10 | recency 0.08 | hist_usefulness 0.07 | workflow 0.05
       → top-k

  ContradictionEngine (new captures vs pinned/existing memories):
    - negation-based detection
    - ≥40% token overlap threshold
    - 0.85 confidence threshold
    - pinned memories always win

  Event-sourced memory (EventLog — idempotent, fcntl-safe):
    9 event types: CREATED, UPDATED, ACCESSED, SCORED, FEEDBACK,
                   MERGED, ARCHIVED, SUPERSEDED, LINKED

  7 typed MemoryObjects (SQLite WAL projection via MemoryStore):
    decision | semantic | operational | procedural | episodic | social | temporal

  Decision memory builder: auto-classifies decisions → MemoryObjects
  Operational memory builder: captures facts (IPs, ports, commands, paths)
  Multi-agent working memory: shared state across agent sessions

  MemoryConsolidator: autonomic loop (DRY-RUN — observes, never mutates)

  REST API: FastAPI on 127.0.0.1:7300 (DEPTHFUSION_REST_API=true)
    - loopback by default; DEPTHFUSION_API_PUBLIC=1 requires DEPTHFUSION_API_TOKEN
```

```
src/depthfusion/
├── core/        — types, config, scoring, feedback (RecallStore, FeedbackResult)
├── fusion/      — rrf (k=60), weighted, block_retrieval, reranker, gates (Mamba B/C/Δ)
├── session/     — tagger (.meta.yaml), scorer, loader, compactor
├── router/      — bus (InMemory/File), publisher, subscriber, dispatcher
├── recursive/   — trajectory, sandbox, strategies, client (rlm)
├── analyzer/    — scanner, compatibility (C1-C11), recommender, installer, prune
├── mcp/         — server (29 tools gated by feature flags)
├── retrieval/   — bm25, reranker (haiku/gemma), hybrid (RRF pipeline), embedding
├── capture/     — auto_learn, compressor, decision_extractor, negative_extractor,
│                  confirm_discovery, dedup, event_hook (high-importance signal)
├── storage/     — vector_store (ChromaDB), tier_manager, decay
├── metrics/     — collector (4 streams), aggregator (backend_summary, capture_summary)
├── backends/    — factory (quality-ranked chains), chain (FallbackChain), null,
│                  haiku, gemma, local_embedding
├── cognitive/   — scorer (8-component), contradiction_engine, event_log,
│                  memory_store (SQLite WAL), memory_objects (7 types),
│                  decision_builder, operational_builder, working_memory,
│                  consolidator (DRY-RUN autonomic loop), rest_api (FastAPI)
└── install/     — install (CLI), migrate (Tier 1 → Tier 2)
```

---

## Install

DepthFusion has three install modes. Pick the one matching your target:

| Mode | Use when | LLM backend | Extras | Step-by-step |
|---|---|---|---|---|
| `local` | Laptop / Windows (GPU auto-detected) | BM25 · GPU embeddings if CUDA present | `[local]` | **Mac/Linux:** inline below · **Windows:** [docs/install/windows-quickstart.md](docs/install/windows-quickstart.md) |
| `vps-cpu` | Cloud VPS, no GPU | Haiku via API | `[vps-cpu]` | **[docs/install/vps-cpu-quickstart.md](docs/install/vps-cpu-quickstart.md)** |
| `vps-gpu` | CUDA host (≥ 20 GB VRAM) | Local Gemma via vLLM | `[vps-gpu]` | **[docs/install/vps-gpu-quickstart.md](docs/install/vps-gpu-quickstart.md)** |
| `mac-mlx` | Apple Silicon Mac (M1/M2/M3/M4) | Local Gemma/Qwen via mlx_lm | `[mac-mlx]` | [docs/mcp-local-setup.html](docs/mcp-local-setup.html) Part E |

The two quickstart guides are the canonical, fully-tested install procedures for non-local hosts. Follow them; the inline `local` snippet is a 2-minute laptop install only.

**Upgrading to v1.2.0?** Pull + `pip install -e .[local]` (or your mode's extras). No schema changes. To enable HNSW: set `DEPTHFUSION_HNSW_ENABLED=true` in `~/.claude/depthfusion.env` and `pip install hnswlib>=0.7` into your venv. HNSW is fully optional — BM25-only recall remains the default.

**Upgrading to v1.1.0?** → **[docs/install/upgrade-to-v1.1.0.md](docs/install/upgrade-to-v1.1.0.md)** — covers E-44 (Windows installer, fcntl compat, CI matrix). Pull + reinstall, no schema changes.

**Upgrading from v1.0.0 (skipping post-v1.0.0)?** Follow [upgrade-to-post-v1.0.0.md](docs/install/upgrade-to-post-v1.0.0.md) (E-38–E-43) first, then [upgrade-to-v1.1.0.md](docs/install/upgrade-to-v1.1.0.md) (E-44), then v1.2.0 above.

> ⚠️ **Billing safety — always use `DEPTHFUSION_API_KEY`, never `ANTHROPIC_API_KEY`.**
> Claude Code reads `ANTHROPIC_API_KEY` as its own auth credential and will switch your **entire** Claude Code billing from your Pro/Max subscription to pay-per-token API for everything — not just DepthFusion. The separate `DEPTHFUSION_API_KEY` exists specifically to prevent this. The installer refuses to use `ANTHROPIC_API_KEY`.

### Prerequisites

- Python ≥ 3.10 (3.10–3.13 supported; Ubuntu 24.04 ships 3.12 by default)
- `pip` ≥ 23.0
- `git` (to clone)
- A working `~/.claude/` directory (Claude Code installed and run at least once)
- For vps-cpu: `ANTHROPIC_API_KEY` available to set as `DEPTHFUSION_API_KEY` (separate variable)
- For vps-gpu: CUDA 12.x + ≥ 20 GB VRAM + `nvidia-smi` on PATH
- For mac-mlx: Apple Silicon Mac (M1 or later), macOS 13+, arm64 Python

### Clone & install

```bash
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion

# Pin to a known-good release.
git checkout v1.1.0

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### Quick install — `local` mode (laptop, zero API cost)

```bash
pip install -e '.[local]'
python3 -m depthfusion.install.install --mode=local

# Register with Claude Code (user scope = available in every project)
# If re-registering after reinstall: claude mcp remove depthfusion -s user
claude mcp add depthfusion -s user ~/projects/depthfusion/scripts/mcp-server.sh

# Verify
claude mcp list | grep depthfusion         # should show "✓ Connected"
ls ~/.claude/depthfusion-metrics/          # should exist after first MCP call
```

**Limitations of `local` mode:** No semantic reranking (BM25 only). Cat-D continuity requires manual `/learn` after each session. No auto-capture (no PostCompact hook). Cognitive layer (E-31) operates in heuristic-only mode without a reranker backend.

### Quick install — `local` mode (Windows)

```powershell
git clone https://github.com/gregdigittal/depthfusion.git $HOME\projects\depthfusion
cd $HOME\projects\depthfusion

# Run the installer (creates venv, installs, registers with Claude Desktop)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer will prompt for your DepthFusion API key (get it from `claude.ai/settings → API Keys`). It will **refuse** a key starting with `sk-ant-api03-` — those are Claude Code's own billing credentials.

Full step-by-step: **[docs/install/windows-quickstart.md](docs/install/windows-quickstart.md)**

### Install — `vps-cpu` mode (recommended for cloud servers)

Follow **[docs/install/vps-cpu-quickstart.md](docs/install/vps-cpu-quickstart.md)** end-to-end (~10 min). It covers:

1. Python venv setup with PEP 668 fallback for Ubuntu 24.04
2. `pip install -e '.[vps-cpu]'` (pulls in `anthropic`, `chromadb`, `fastapi`)
3. `python3 -m depthfusion.install.install --mode=vps-cpu --api-key="$DEPTHFUSION_API_KEY"`
4. MCP server registration in `~/.claude.json` (user-level, NOT `~/.claude/settings.json`)
5. SessionStart, PreCompact, PostCompact hook installation in `~/.claude-shared/hooks/`
6. Cognitive layer env vars copied from `depthfusion.env` (all E-31 flags ON)
7. Weekly regression-monitor systemd timer (CIQS comparison every Sunday)
8. Verification: trigger a real session, confirm `~/.claude/depthfusion-metrics/<today>-recall.jsonl` populates

### Install — `vps-gpu` mode (CUDA host)

Follow **[docs/install/vps-gpu-quickstart.md](docs/install/vps-gpu-quickstart.md)** end-to-end (~4 hrs, mostly Gemma model download). It covers:

1. CUDA + driver verification (`nvidia-smi`)
2. `pip install -e '.[vps-gpu]'` (pulls in `sentence-transformers`, plus `vllm` separately)
3. vLLM systemd service for local Gemma inference (root-level)
4. `python3 -m depthfusion.install.install --mode=vps-gpu`
5. Local embedding model download (sentence-transformers cache)
6. Hook installation and cognitive layer env vars as in vps-cpu
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

## Full Feature Set (v1.0.0)

### Retrieval pipeline

- **BM25 lexical retrieval** — block-level chunking, k1=1.5, b=0.75 defaults, project-scoped IDF
- **Haiku semantic reranker** (vps-cpu) — top-10 → top-k via Anthropic API
- **Gemma reranker** (vps-gpu) — same role, local inference via vLLM
- **Local embeddings** (vps-gpu) — sentence-transformers, cosine similarity
- **ChromaDB vector store** (Tier 2, 500+ sessions) — HNSW ANN over embeddings
- **RRF fusion** (k=60) — combines BM25 and vector results when both engaged
- **Selective fusion gates** (S-51/S-129, Mamba B/C/Δ) — α-blended source weighting; full Python parity with TypeScript implementation; opt-in via `DEPTHFUSION_FUSION_GATES_ENABLED=true`
- **Materialisation policy** (S-130) — three-gate pipeline (score threshold → novelty cosine gate → capacity eviction) for selective persistence of high-value chunks; wired to `RecallPipeline` when fusion gates are enabled
- **Chunk state compression** (S-130) — `ChunkStateCompressor` maintains Mamba-style fixed-size boundary state (topic EMA, entity LRU, score stats, exponential decay) across multi-call recall boundaries
- **Sub-project scoping** (S-122, Wing/Room) — `DEPTHFUSION_WING_ID` / `DEPTHFUSION_ROOM_ID` confine recall and capture to a logical sub-project partition within a shared `~/.claude/` corpus
- **Quality-ranked fallback chains** (S-44 / DR-018 §4) — Gemma → Haiku → Null with typed-error rerouting
- **Cross-project / project-scoped recall** — defaults to current project; `cross_project=true` searches all
- **CognitiveScorer** (E-31) — 8-component ranking layer applied after RRF; ~15 ms overhead

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
| `YYYY-MM-DD-recall.jsonl` | Per-query recall: `backend_used`, `backend_fallback_chain`, `latency_ms_per_capability`, `total_latency_ms`, `result_count`, `config_version_id`, `cognitive_score_components` |
| `YYYY-MM-DD-capture.jsonl` | Per-write capture: `capture_mechanism`, `file_path`, `chars_written`, `event_subtype`, `config_version_id` |
| `YYYY-MM-DD-gates.jsonl` | Mamba B/C/Δ gate audit (opt-in via `DEPTHFUSION_FUSION_GATES_ENABLED=true`) |

Aggregation tools: `MetricsAggregator.backend_summary()` (per-backend latency + error rates + `skipped_lines`), `MetricsAggregator.capture_summary()` (per-mechanism write rates + `skipped_lines`). Both summaries include `skipped_lines` (E-41) for data-integrity visibility — non-zero indicates malformed JSONL lines that were silently dropped.

Runbook: **[docs/runbooks/dogfood-telemetry.md](docs/runbooks/dogfood-telemetry.md)** for the weekly self-audit protocol.

### Recursive LLM (rlm, E-04)

- `run_recursive` MCP tool — multi-step reasoning with sandbox isolation
- Trajectory tracking, retry strategies, cost ceiling enforcement
- Optional dependency: `rlm` package

### Compatibility constraints (C1–C11)

11 invariants protecting the existing Claude Code infrastructure (CLaRa, hook chain, `~/.claude/` layout). Run `python -m depthfusion.analyzer.compatibility` for a live check. Currently 10 GREEN · 1 YELLOW (benign).

---

## E-31 Cognitive Infrastructure Layer

E-31 (Structured Evolving Cognition) is the major v1.0.0 addition. It sits above the existing retrieval pipeline and gives DepthFusion structured, typed, contradiction-aware memory.

### CognitiveScorer — 8-component ranking

Every recalled chunk is re-ranked by a weighted composite:

| Component | Weight | What it measures |
|---|---|---|
| `semantic` | 0.25 | Embedding similarity to the query |
| `lexical` | 0.18 | BM25 term overlap |
| `confidence` | 0.15 | Extractor confidence score of the original capture |
| `regime` | 0.12 | Stability of surrounding context at capture time |
| `graph` | 0.10 | Entity graph connectivity to query entities |
| `recency` | 0.08 | Decay-weighted age |
| `hist_usefulness` | 0.07 | Historical feedback signal (used vs ignored) |
| `workflow` | 0.05 | Match to current agent workflow phase |

### ContradictionEngine

Fires on every new capture and compares it against pinned and recent memories:
- Negation-based detection (token-level)
- ≥40% token overlap threshold to qualify as a candidate conflict
- 0.85 confidence threshold before raising a contradiction
- Pinned memories always win — a new capture cannot silently overwrite a pinned belief

### Event-sourced memory (EventLog)

All memory mutations flow through an append-only, fcntl-safe EventLog. 9 event types:

`CREATED` · `UPDATED` · `ACCESSED` · `SCORED` · `FEEDBACK` · `MERGED` · `ARCHIVED` · `SUPERSEDED` · `LINKED`

The EventLog is the source of truth; the MemoryStore (SQLite WAL) is a read-optimised projection.

### 7 typed MemoryObjects

| Type | Captures |
|---|---|
| `decision` | Architectural and implementation decisions |
| `semantic` | Conceptual knowledge, definitions, relationships |
| `operational` | Facts: IPs, ports, file paths, commands |
| `procedural` | How-to sequences and runbooks |
| `episodic` | Session-specific events and outcomes |
| `social` | User preferences, team conventions, feedback |
| `temporal` | Time-bound facts with expiry semantics |

### MemoryConsolidator (autonomic loop)

Runs periodically in DRY-RUN mode — observes what it would merge or archive but never mutates the store. Enables safe monitoring before you opt into write mode (a future flag when the system has enough production history).

### REST API

When `DEPTHFUSION_REST_API=true`, a FastAPI server starts on `127.0.0.1:7300`. Loopback-only by default. Set `DEPTHFUSION_API_PUBLIC=1` **only** with `DEPTHFUSION_API_TOKEN` configured — the server will refuse to start public without a token.

For persistent operation, use the bundled systemd user service:

```bash
cp ~/projects/depthfusion/infra/systemd/depthfusion-rest.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now depthfusion-rest
```

A generated Go CLI (`depthfusion-pp-cli`) and MCP server (`depthfusion-pp-mcp`) expose all 29 REST endpoints as subcommands and agent tools. See **[docs/cli.md](docs/cli.md)** for install and usage.

---

## MCP Tools (29 total)

### Core tools (pre-E-31, 18 tools)

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
| `depthfusion_set_memory_score` | Override importance / salience | always |
| `depthfusion_recall_feedback` | Apply bounded salience deltas from used/ignored chunks | always |
| `depthfusion_pin_discovery` | Exempt a discovery from age-based pruning | always |
| `depthfusion_describe_capabilities` | Which retrieval layers + CMs engage in this instance | always |
| `depthfusion_inspect_discovery` | Parsed frontmatter of a discovery file | always |

### E-31 Cognitive tools (6 new tools)

| Tool | Description | Required flag |
|---|---|---|
| `df_retrieve_context` | Cognitive-scored recall with 8-component ranking | `cognitive_retrieval` |
| `df_record_decision` | Write a typed decision MemoryObject | `decision_memory` |
| `df_record_incident` | Write an incident/error MemoryObject | `operational_memory` |
| `df_mark_superseded` | Mark a prior decision superseded by a new one | `decision_memory` |
| `df_report_outcome` | Record outcome of a past decision (feedback loop) | `decision_memory` |
| `df_get_cognitive_state` | Current cognitive layer health + active memory count | always |

### Post-E-31 tools (5 new tools — E-33, E-34, E-35, E-45)

| Tool | Description | Required flag |
|---|---|---|
| `depthfusion_record_telemetry` | Log a per-tool-call telemetry event (cost, latency, usage) | always |
| `depthfusion_query_telemetry` | Aggregate telemetry by project, agent, story, sprint, or period | always |
| `depthfusion_surface_skill_candidates` | Scan telemetry for recurring patterns; draft candidate skills in SkillForge | always |
| `depthfusion_session_seed` | Run a seed recall at session start; publish results as high-priority context | always |
| `depthfusion_hnsw_capability` | Report HNSW index capability and state (agent-ops bridge startup probe) | always |

Full tool documentation with response shapes: see `docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md` §2.

The generated CLI (`depthfusion-pp-cli`) exposes all 29 tools as subcommands. See **[docs/cli.md](docs/cli.md)**.

---

## Shared Memory Fabric (E-46, v0.6.0-alpha)

Every memory publication, subscription, and recall becomes a first-class node in the knowledge graph. Any session can ask "who knew what, when" — and new sessions inherit the room's working memory automatically.

**Three pain points it solves:**
1. Agents in the same project duplicate work because they can't see each other's in-progress discoveries.
2. A new session starts cold even though five other agents have been building context all morning.
3. There's no audit trail for "which agent introduced this assumption into the shared context?"

### Quickstart (5 commands)

```bash
# 1. Start the REST server (requires DEPTHFUSION_API_TOKEN for any non-loopback bind)
DEPTHFUSION_API_TOKEN=mytoken uvicorn depthfusion.api.rest:app --port 7300

# 2. Subscribe to the live event stream (terminal 1)
curl -N -H "Authorization: Bearer mytoken" \
  "http://localhost:7300/v1/events/stream?projects=myproject&consumer_id=agent-b"

# 3. Publish a memory event (terminal 2)
curl -X POST -H "Authorization: Bearer mytoken" -H "Content-Type: application/json" \
  -d '{"agent_id":"agent-a","project_slug":"myproject","memory_refs":["abc123"]}' \
  http://localhost:7300/v1/events/publish

# 4. Seed a new session with the room's working memory
curl -H "Authorization: Bearer mytoken" \
  "http://localhost:7300/v1/events/seed?projects=myproject&goal=implement+auth"

# 5. Query provenance: who has seen memory abc123?
curl -H "Authorization: Bearer mytoken" \
  "http://localhost:7300/v1/graph/memory/abc123/observers"
```

Full documentation: **[docs/fabric/api-reference.md](docs/fabric/api-reference.md)** · [Tailscale setup](docs/fabric/tailscale-setup.md) · [Kafka/Flink migration](docs/fabric/kafka-flink-migration.md)

---

## Feature Flags

### Core flags (pre-E-31)

| Env Var | Controls | Default |
|---|---|---|
| `DEPTHFUSION_MODE` | `local` / `vps-cpu` / `vps-gpu` / `mac-mlx` | `local` |
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
| `DEPTHFUSION_PRUNE_SUPERSEDED_MIN_AGE_HOURS` | Grace period before archiving superseded files (E-42) | `0` |
| `DEPTHFUSION_RLM_COST_CEILING` | Per-call rlm cost ceiling (USD) | `0.50` |
| `DEPTHFUSION_EMBEDDING_BACKEND` | `local` / `null` (vps-gpu only) | `local` (vps-gpu) |
| `DEPTHFUSION_LINEAR_BLEND` | Replace RRF with α-weighted linear fusion (E-38 S-121) | `false` |
| `DEPTHFUSION_WING_ID` | Sub-project wing scope for recall/capture (E-38 S-122) | — |
| `DEPTHFUSION_ROOM_ID` | Sub-project room scope within a wing (E-38 S-122) | — |
| `DEPTHFUSION_SKILLFORGE_API_URL` | SkillForge base URL for recursive calls (E-39) | — |
| `DEPTHFUSION_SKILLFORGE_API_TOKEN` | Bearer token for SkillForge API (E-39) | — |
| `DEPTHFUSION_SKILLFORGE_RECURSIVE_SKILL_ID` | UUID of pre-registered SkillForge skill (E-39) | — |

### E-45 HNSW flags

| Env Var | Controls | Default |
|---|---|---|
| `DEPTHFUSION_HNSW_ENABLED` | Enable HNSW index + fused BM25+vector recall | `false` |
| `DEPTHFUSION_HNSW_INDEX_PATH` | Directory for HNSW index files + sidecars | `~/.depthfusion/hnsw/` |
| `DEPTHFUSION_EMBEDDING_MODEL` | sentence-transformers model for HNSW embeddings | `all-MiniLM-L6-v2` |

### E-31 Cognitive flags (all ON in the canonical depthfusion.env)

| Env Var | Controls | Default |
|---|---|---|
| `DEPTHFUSION_COGNITIVE_RETRIEVAL` | 8-component CognitiveScorer in the recall pipeline | `false` |
| `DEPTHFUSION_COGNITIVE_SCORING` | Same scorer, hybrid pipeline wiring | `false` |
| `DEPTHFUSION_LLM_CLASSIFIER` | LLM-based memory type classifier | `false` |
| `DEPTHFUSION_CONTRADICTION_ENGINE` | ContradictionEngine on every new capture | `false` |
| `DEPTHFUSION_DECISION_MEMORY` | Decision MemoryObject builder + related MCP tools | `false` |
| `DEPTHFUSION_OPERATIONAL_MEMORY` | Operational fact memory builder | `false` |
| `DEPTHFUSION_MULTI_AGENT_WM` | Shared working memory across agent sessions | `false` |
| `DEPTHFUSION_AUTONOMIC` | MemoryConsolidator autonomic loop (DRY-RUN) | `false` |
| `DEPTHFUSION_REST_API` | FastAPI REST endpoint on 127.0.0.1:7300 | `false` |

> The defaults above are what ships in the package. The canonical `depthfusion.env` installed by the quickstart guides sets all E-31 flags to `true` for v1.0.0 production installs.

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

pytest                              # 1993 tests, GREEN (a few skipped if chromadb/cuda absent)
pytest tests/test_metrics/ -q       # 116 tests — observability (includes E-41 reliability tests)
pytest tests/test_backends/ -q      # 225 tests — backend chain + factory
pytest tests/test_cognitive/ -q     # 175 tests — E-31 cognitive layer
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
- `fastapi` + `uvicorn` — `[vps-cpu]` and `[vps-gpu]` extras; required for `DEPTHFUSION_REST_API`
- `mlx-lm` ≥ 0.18 — `[mac-mlx]` extra; Apple Silicon LLM inference via mlx_lm.server
- `vllm` — `[vps-gpu]`-adjacent; installed separately via the vps-gpu quickstart (see runbook)
- `rlm` — optional; install from source for recursive LLM support

```bash
pip install -e '.[local]'     # Laptop, zero external deps
pip install -e '.[vps-cpu]'   # Cloud VPS, Haiku API + FastAPI REST
pip install -e '.[vps-gpu]'   # CUDA host, local Gemma + embeddings + FastAPI REST
pip install -e '.[mac-mlx]'   # Apple Silicon, local GPU inference (arm64 macOS only)
```

The legacy `vps-tier1` / `vps-tier2` extras were removed in v0.6.0 (see S-56 / S-57). Use the three-mode extras above.

---

## Project status & roadmap

- **Closed (v1.0.0):** 51 user stories across E-01 through E-31. E-31 (Structured Evolving Cognition) ships complete in v1.0.0.
- **Closed (post-v1.0.0 on `main`):** E-38 MemPalace integration (temporal filter, KG provenance, linear blend, Wing/Room scoping, KG edge invalidation), E-39 SkillForge SF-2 integration, E-40 CIQS Cat D benchmark harness, E-41 metrics reliability (flock guard + skipped_lines), E-42 pruner grace period, E-43 SkillForge divergence gap resolution (JWT refresh + Mamba Python port).
- **Active (calendar-gated):** S-79 AC-2/AC-4 and S-80 AC-4 await ≥ 5 days of dogfood emissions; observability ACs only.
- **Backlog:** E-26 CIQS Cat D AC-3 — benchmark-blocked (requires live corpus + eval set). MemoryConsolidator write mode (currently DRY-RUN) — planned after 30 days of production autonomic observation.
See `BACKLOG.md` for the full ledger.
