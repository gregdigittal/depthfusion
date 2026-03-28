# DepthFusion v0.3.0 — Design Spec
# Date: 2026-03-28 | Source: /goal autonomous brainstorm

---

## Objective

Augment DepthFusion beyond BM25 by integrating auto-capture, semantic reranking, and a
two-tier VPS architecture. The result must be demonstrably better than v0.2.0 (CIQS 83–85)
and competitive with Mem0 / claude-mem while remaining offline-capable in local mode.

---

## Architecture Decisions (reasoned, not assumed)

### 1. Vector Store: ChromaDB
ChromaDB runs fully embedded (no server process), pip-installable, sqlite3-backed. Qdrant
requires Docker or a binary. For a single-tenant VPS: ChromaDB is the only sensible choice.

### 2. Tier 1 Semantic Layer: Haiku Reranker (not raw embeddings)
Claude API has no embedding endpoint. AI Gateway requires separate setup. sentence-transformers
adds ~400MB (torch + model) for Tier 1's modest needs. Instead: pass top-10 BM25 results to
claude-haiku-4-5 with a relevance ranking prompt. Semantic understanding from an existing
ANTHROPIC_API_KEY, no new dependencies, ~$0.00025/query, ~500ms.

This is qualitatively *better* than cosine similarity for technical content because it handles
synonyms, paraphrasing, and context that embedding distance cannot.

### 3. Tier 2 Embeddings: ChromaDB Default (sentence-transformers all-MiniLM-L6-v2)
ChromaDB auto-downloads this model on first use (~80MB). Since sentence-transformers is already
a transitive dep of chromadb, Tier 2 embeddings add zero new installs. Initial retrieval via
vector similarity, then haiku reranker on top.

### 4. Auto-Capture: PreCompact + PostCompact Hook Pair
PreCompact: snapshot {branch, task, last-5-decisions, active-plan-path} to
`~/.claude/.depthfusion-compact-snapshot.json` before context is lost.
PostCompact: read snapshot, call haiku to summarize compacted session content, write
structured discovery file to `~/.claude/shared/discoveries/{date}-{project}-autocapture.md`.
No background process. Fires naturally during normal Claude Code usage.

### 5. Tier Promotion: Automatic with Configurable Override
`DEPTHFUSION_TIER_AUTOPROMOTE=true` (default for VPS install, false for local).
Promotion is non-destructive — Tier 1 files remain accessible as BM25 fallback.
Logs promotion event to discoveries/ for future recall.

---

## Install Modes

### Local Mode (`python install.py --mode local`)
- BM25 retrieval (v0.2.0 implementation, unchanged)
- PostCompact hook: heuristic auto-learn extraction (regex patterns, no API calls)
  - Extracts lines matching: `→`, `DECISION:`, `NOTE:`, `IMPORTANT:`, `WARNING:`, `## `
  - Writes to `~/.claude/projects/{project}/memory/auto-learned-{date}.md`
- DEPTHFUSION_TIER_AUTOPROMOTE=false (default)
- Zero new Python dependencies

### VPS Mode (`python install.py --mode vps`)

**Tier 1 (< DEPTHFUSION_TIER_THRESHOLD sessions, default 500):**
- BM25 initial retrieval (top-10)
- Haiku reranker: BM25 top-10 → haiku relevance ranking → top-3 returned
- PreCompact hook: snapshot active state before compaction
- PostCompact hook: haiku session summarization → discoveries/
- Session compressor: on-demand .tmp → structured discovery file (haiku-tier)
- DEPTHFUSION_RERANKER_ENABLED=true (can disable with env var)

**Tier 2 (≥ DEPTHFUSION_TIER_THRESHOLD sessions):**
- ChromaDB vector retrieval (top-20 candidates)
- BM25 retrieval (top-10 candidates)
- RRF fusion of vector + BM25 ranks (k=60) — both retrievers independently competent now
- Haiku reranker on fused top-10 → final top-3
- Hourly background indexer (cron): indexes new sessions into ChromaDB since last run
- Auto-promotion from Tier 1 when corpus crosses threshold
- Migration script: `python -m depthfusion.install.migrate`

---

## New Module Structure

```
src/depthfusion/
├── retrieval/                     (NEW package)
│   ├── __init__.py
│   ├── bm25.py                    — extracted _BM25 class + tokenizer from server.py
│   ├── reranker.py                — haiku-based relevance reranker (Tier 1+2)
│   └── hybrid.py                  — RRF fusion + pipeline orchestrator
├── capture/                       (NEW package)
│   ├── __init__.py
│   ├── auto_learn.py              — heuristic extractor (local) + haiku summarizer (VPS)
│   └── compressor.py              — .tmp file → structured discovery file (haiku)
├── storage/                       (NEW package)
│   ├── __init__.py
│   ├── vector_store.py            — ChromaDB persistent client wrapper (Tier 2)
│   └── tier_manager.py            — corpus size detection, tier routing, promotion
└── install/                       (NEW package)
    ├── __init__.py
    ├── install.py                 — CLI: --mode local|vps --tier-threshold N --dry-run
    └── migrate.py                 — Tier 1 → Tier 2 ChromaDB indexing with progress bar
```

---

## New MCP Tools (added to server.py)

| Tool | Description |
|------|-------------|
| `depthfusion_tier_status` | Corpus size, active tier, threshold, sessions until promotion |
| `depthfusion_auto_learn` | Manually trigger auto-learning from recent session files |
| `depthfusion_compress_session` | Compress a specific .tmp file into a discovery file |

---

## New Hooks

| Hook | Trigger | Action |
|------|---------|--------|
| `depthfusion-pre-compact.sh` | PreCompact | Write compact snapshot JSON |
| `depthfusion-post-compact.sh` | PostCompact | Haiku summarize → discoveries/ |

---

## New pyproject.toml Optional Dependencies

```toml
[project.optional-dependencies]
vps-tier1 = ["anthropic>=0.40"]   # haiku reranker (may already be installed)
vps-tier2 = ["chromadb>=0.4"]     # vector store + embeddings
dev = [...]                        # unchanged
```

Note: anthropic SDK may already be installed system-wide. Tier 1 works with any
existing anthropic installation; it does NOT need to be added to depthfusion's own deps
if it's already available in the environment.

---

## CIQS Targets

| Condition | CIQS Target | Category D Target |
|-----------|-------------|-------------------|
| Local | ≥ 83 (no regression) | ≥ 42% (heuristic auto-learn only) |
| VPS Tier 1 | ≥ 88 | ≥ 65% (haiku auto-capture) |
| VPS Tier 2 | ≥ 90 | ≥ 70% (vector + haiku) |

---

## Constraints

- Do NOT add cloud storage backends
- Do NOT implement knowledge graph entity linking (v0.4.0)
- Do NOT remove BM25 path from any mode
- Do NOT break C1-C11 compatibility constraints
- DEPTHFUSION_TIER_THRESHOLD must be configurable (SkillForge/DepthForge bundling)
- Auto-capture hook makes API calls in VPS mode — MUST be opt-in for local mode
- All 286 existing tests must remain GREEN

---

## Features Sourced From Competing Tools

| Source | Feature | Implementation |
|--------|---------|----------------|
| claude-mem | Auto session compression | compressor.py via haiku API |
| Mem0 | Auto-capture without manual /learn | PostCompact hook + auto_learn.py |
| Memory Anchor | Checkpoint/resume for interrupted sessions | PreCompact + PostCompact pair |
| Mem0 | Semantic search | haiku reranker (Tier 1) + ChromaDB (Tier 2) |
| mcp-memory-service | Knowledge graph | DESCOPED → v0.4.0 |
