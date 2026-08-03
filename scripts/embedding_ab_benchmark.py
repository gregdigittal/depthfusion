#!/usr/bin/env python3
"""
Embedding A/B Benchmark — S-249 / T-<task>

Standalone benchmark script (NOT extending retrieval_benchmark.py or ciqs_compare.py).
Compares two sentence-transformers embedding models on a deterministic synthetic corpus
of agent-memory-style items (code snippets, decisions, error traces, config facts).

Usage:
    python scripts/embedding_ab_benchmark.py \
        --model-a all-MiniLM-L6-v2 \
        --model-b bge-small-en-v1.5 \
        --output-dir docs/agent-outputs

Metrics: recall@1, recall@3, recall@5, MRR, NDCG
No API key required — sentence-transformers local models only.
"""

from __future__ import annotations

import argparse
import datetime
import math
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Corpus generation — deterministic, seeded, no external data dependency
# ---------------------------------------------------------------------------

_SEED = 42

# Templates for agent-memory-style documents (S-249 / AC-3)
_CODE_SNIPPETS = [
    "def apply_decay(score: float, age_days: int) -> float:\n    return score * (0.9 ** age_days)",
    "async def _process_request(request: dict) -> dict:\n    loop = asyncio.get_event_loop()\n    return await loop.run_in_executor(None, _sync_handler, request)",
    "class RedisStreamBackend:\n    def append(self, key: str, data: bytes) -> str:\n        return self._client.xadd(key, {'data': data})",
    "def dedup_against_corpus(new_item: str, corpus: list[str], threshold: float = 0.85) -> bool:\n    scores = model.similarity(new_item, corpus)\n    return bool((scores > threshold).any())",
    "def identify_candidates(items: list[dict], max_age_days: int = 90) -> list[dict]:\n    return [i for i in items if i['age_days'] > max_age_days]",
    "from sentence_transformers import SentenceTransformer\nmodel = SentenceTransformer('all-MiniLM-L6-v2')",
    "def recall_at_k(relevant: set, ranked: list, k: int) -> float:\n    return len(set(ranked[:k]) & relevant) / max(len(relevant), 1)",
    "async def stream_events(session_id: str) -> AsyncIterator[dict]:\n    queue = _MCP_SESSIONS[session_id]\n    while True:\n        yield await queue.get()",
    "def embed_batch(texts: list[str], model) -> np.ndarray:\n    return model.encode(texts, normalize_embeddings=True, batch_size=64)",
    "def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:\n    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))",
    "class EventStore:\n    def __init__(self, backend: StreamBackend):\n        self._backend = backend",
    "def require_principal(token: str, jwks_url: str) -> dict:\n    payload = jwt.decode(token, key=fetch_jwks(jwks_url), algorithms=['RS256'])\n    return payload",
    "app = FastAPI(title='DepthFusion MCP')\n\n@app.get('/health')\nasync def health() -> dict:\n    return {'status': 'ok'}",
    "def atomic_frontmatter_rewrite(path: Path, updates: dict) -> None:\n    tmp = path.with_suffix('.tmp')\n    tmp.write_text(updated_content)\n    tmp.replace(path)",
    "def pinned_discovery(item: dict, pin: bool = True) -> dict:\n    item['pinned'] = pin\n    item['pinned_at'] = datetime.utcnow().isoformat()\n    return item",
]

_DECISIONS = [
    "Decision: Use max(per-entry confidence) for file-level importance, not mean average. Avoids dilution by low-quality entries. See S-70 ADR.",
    "Decision: MCP tools are thin wrappers in mcp/tools/*.py registered via _registry.py. Business logic lives in _shared.py impl functions.",
    "Decision: Loopback-only bind (127.0.0.1) by default for all datastore ports. Public bind requires explicit justification + auth + firewall rule.",
    "Decision: Fable-5 vendor isolation — Dev and code review for the same task MUST use different vendors. Same-vendor review is forbidden.",
    "Decision: Durable agent outputs go to docs/agent-outputs/ (tracked in git). Ephemeral scratch goes to .agent-hub/outputs/ (gitignored).",
    "Decision: BACKLOG.md is sole authoritative source for epic status. CLAUDE.md active-epic list must sync on every epic completion commit.",
    "Decision: sentence-transformers local models preferred over API calls for offline-capable retrieval benchmark scripts.",
    "Decision: EventStore uses runtime_checkable Protocol (StreamBackend) — new backends implement Protocol rather than modifying EventStore.",
    "Decision: Two-lock model for FeedbackStore — per-file read lock + global write lock to prevent concurrent frontmatter corruption.",
    "Decision: GraphBackend Protocol mirrors StreamBackend pattern — consistent extension point across core/event_store.py and graph layer.",
    "Decision: Use conventional commits (feat/fix/chore/docs/refactor) with 72-char subject limit and imperative mood.",
    "Decision: Optional dependency extras (local / vps-cpu / vps-gpu) in pyproject.toml with CVE/task-ID-annotated security lower bounds.",
]

_ERROR_TRACES = [
    "ERROR: autocompact thrashing detected. Context refilled within 3 turns of previous compact. Root cause: full read of BACKLOG.md (5000+ lines). Fix: use grep-only access pattern.",
    "ERROR: 403 Forbidden from mcp.tonracein.com/mcp — Bearer token missing from Authorization header. Check MCP_TOKEN env var is set.",
    "ERROR: Redis XADD failed — WRONGTYPE Operation against a key holding the wrong kind of value. Stream key collides with existing string key.",
    "ERROR: sentence_transformers.SentenceTransformerTrainingArguments — model download failed, no network access. Use cached models with TRANSFORMERS_OFFLINE=1.",
    "ERROR: ChromaDB collection 'depthfusion' not found. Run `python scripts/backfill_acl.py` to initialise the collection before ingestion.",
    "ERROR: pytest timeout after 30s in test_session_seed — asyncio event loop not closed cleanly between tests. Add @pytest.mark.asyncio(loop_scope='function').",
    "ERROR: uvicorn startup failed — address 0.0.0.0:8000 already in use. Another instance is running. Kill with: pkill -f uvicorn",
    "ERROR: jwt.exceptions.ExpiredSignatureError — OIDC token expired. Re-authenticate and update MCP_TOKEN env var.",
    "ERROR: ModuleNotFoundError: No module named 'depthfusion'. Install with: pip install -e '.[local]' from project root.",
    "ERROR: git push rejected — non-fast-forward update. Pull and rebase feat branch on main before pushing: git pull --rebase origin main",
    "ERROR: hnswlib index not found at path ~/.depthfusion/index.bin. Run depthfusion_ingest_project to build the index first.",
    "ERROR: Tauri build failed — pnpm install missing lockfile. Run: cd app && pnpm install --frozen-lockfile",
    "ERROR: DecayJob — no items found matching age threshold. Check that events are being ingested via depthfusion_ingest_conversation.",
]

_CONFIG_FACTS = [
    "Config: MCP server default port 8080, bind 127.0.0.1. Override with MCP_HOST and MCP_PORT env vars.",
    "Config: TRANSFORMERS_CACHE defaults to ~/.cache/huggingface/hub. Set SENTENCE_TRANSFORMERS_HOME for custom cache location.",
    "Config: DepthFusion default embedding model: all-MiniLM-L6-v2 (384 dims). Upgrade path: bge-small-en-v1.5 (384 dims) or all-mpnet-base-v2 (768 dims).",
    "Config: Redis Streams backend URL: redis://localhost:6379/0. Override with REDIS_URL env var.",
    "Config: Decay half-life default 30 days. Configurable via DEPTHFUSION_DECAY_HALFLIFE_DAYS.",
    "Config: ChromaDB collection name: 'depthfusion'. Path: ~/.depthfusion/chroma. Override with CHROMA_PATH.",
    "Config: JWT validation — JWKS URL from OIDC_JWKS_URL env var, audience from OIDC_AUDIENCE. Falls back to legacy DEPTHFUSION_TOKEN.",
    "Config: GitHub Actions CI matrix — Python 3.11 only, Ubuntu latest, cache pip dependencies with poetry lock hash.",
    "Config: Tauri app bundle identifier: com.depthfusion.app. Icon set in app/src-tauri/icons/.",
    "Config: pytest-asyncio mode=auto in pyproject.toml. Tests under tests/core, tests/e2e, tests/benchmarks.",
    "Config: structlog configured with JSON renderer in production, ConsoleRenderer in development. Log level from LOG_LEVEL env var.",
    "Config: DepthFusion project slug max 64 chars, lowercase alphanumeric + hyphens only. Validated at registration.",
    "Config: Memory score range [0.0, 1.0]. Pinned items get score boost of +0.2. Decayed items pruned below 0.05.",
    "Config: HNSWLIB index params — ef_construction=200, M=16, ef_search=100. Rebuild index when corpus size doubles.",
    "Config: Session checkpoint interval: 30 minutes. DepthFusion publish tags include project_slug, agent_slug, date, task_id.",
]


def build_corpus(rng: random.Random) -> list[dict]:
    """Build ~200-doc agent-memory-style corpus deterministically."""
    docs = []
    categories = [
        ("code", _CODE_SNIPPETS),
        ("decision", _DECISIONS),
        ("error", _ERROR_TRACES),
        ("config", _CONFIG_FACTS),
    ]

    for cat_name, templates in categories:
        for i, tmpl in enumerate(templates):
            # Original template doc
            docs.append({"id": f"{cat_name}_{i}", "category": cat_name, "text": tmpl})
            # Paraphrased variant (keeps category signal, adds diversity)
            filler_words = ["Note:", "Context:", "Recall:", "Observed:", "Important:"]
            prefix = rng.choice(filler_words)
            docs.append({
                "id": f"{cat_name}_{i}_v2",
                "category": cat_name,
                "text": f"{prefix} {tmpl}",
            })

    # Add filler docs to reach ~200 total
    filler_texts = [
        "The agent processed 42 items in the last ingestion batch.",
        "Session ID dc3f9a21 started at 2026-08-01T10:00:00Z.",
        "Graph traversal returned 7 nodes within 2 hops of the root.",
        "Telemetry event: recall_query latency p95=120ms over last 100 calls.",
        "Pinned item: 'Redis XADD pattern for append-only streams' — pinned by user on 2026-07-15.",
        "Feedback recorded: thumbs_up for recall result rank=1, session_id=abc123.",
        "Auto-learn triggered: 3 new patterns extracted from session transcript.",
        "Publish context: tags=['depthfusion', 'pm', '2026-08-03', 'S-249', 'checkpoint'].",
        "Graph edge added: decision_001 -> config_003 (type: implements).",
        "Dedup check: similarity 0.91 > threshold 0.85, item suppressed as duplicate.",
        "Pruner identified 5 candidates for eviction (age > 90 days, score < 0.1).",
        "Model recommendation: all-mpnet-base-v2 for higher-quality local retrieval.",
        "Branch reconciliation: feat/e73-s246-s252 is AHEAD 6 commits from main.",
        "Review gate: PASS — 0 Critical, 2 High fixed, 3 Medium deferred.",
        "CI pipeline: all 47 tests passed in 38s on python3.11 / ubuntu-latest.",
        "DepthFusion version: v2.4.0. Released 2026-08-01. Embedding benchmark added in S-249.",
        "Ingest queue depth: 0. Last flush: 2026-08-03T09:15:00Z. Next scheduled: 2026-08-04T03:00:00Z.",
        "Corpus snapshot: 1847 items ingested, 23 pinned, 142 decayed below threshold.",
        "Recall feedback loop: 312 positive signals, 18 negative signals over last 30 days.",
        "Graph node count: 1203. Edge count: 4512. Average degree: 3.75.",
        "HNSWLIB index rebuilt at 2026-07-28T04:00:00Z. 1847 vectors, 384 dims, M=16.",
        "Session seed applied: project_slug=depthfusion, focus_areas=['retrieval', 'benchmark'].",
        "Auto-compress triggered: session size exceeded 80k tokens. Summary written to disk.",
        "DepthFusion graph status: HEALTHY. Last update: 2026-08-03T08:30:00Z.",
        "Memory score updated: item_id=err_redis_001 score=0.62 (was 0.45, feedback boost).",
        "Provider list: anthropic (primary), openrouter (fallback), local-vllm (offline).",
        "Tag session: tags=['depthfusion', 'benchmark', 'S-249', '2026-08-03'] applied.",
        "Discovery confirmed: pattern 'asyncio.get_event_loop in non-async context' added.",
        "Decision record: D-4 — durable doc routing to docs/agent-outputs/. Ratified 2026-07-26.",
        "Incident record: I-2026-04-28 — Redis port 6379 exposed publicly on 176.9.147.206. Fixed.",
        "Telemetry: ingest_conversation latency p50=45ms p95=180ms p99=420ms (last 1000 calls).",
        "Research topic: 'embedding model dimensionality vs retrieval quality tradeoff'.",
        "Context published: 3 chunks ingested from session transcript. Score: 0.78.",
        "Scope set: project_slug=depthfusion, focus=['s249', 'benchmark', 'sentence-transformers'].",
        "MCP tool called: depthfusion_recall_relevant, query='embedding model comparison', top_k=5.",
        "Project registered: slug=depthfusion, path=/home/gregmorris/projects/depthfusion.",
        "Superseded: context chunk from 2026-07-01 replaced by more recent benchmark findings.",
        "Graph traversal depth=3 from node 'S-249': reached 14 related items.",
        "Model elected: sonnet-4-6 (cheapest capable for planning phase, subscription account).",
        "Report outcome: S-249 implementation complete. AC-1 through AC-4 all satisfied.",
        "Recall feedback: item 'EventStore Protocol pattern' rated 5/5, promoted to score=0.95.",
        "Bridge scanner parsed BACKLOG.md: 52 epics, 312 stories, 847 tasks found.",
        "Sprint velocity: 23 story points completed in last 2 weeks. E-73 at 68% completion.",
        "Agent-hub checkpoint: 4 workers active, 1 blocked on user input, 2 complete.",
        "Post-session sync: depthfusion/2026-08-03-s249-benchmark.md committed and pushed.",
        "Worktree created: feat/e73-s246-s252 at /tmp/worktree-e73. Clean checkout.",
        "PR opened: feat/e73-s246-s252 -> main. Title: 'feat(benchmark): S-249 embedding A/B'.",
        "Conventional commit: feat(scripts): add embedding_ab_benchmark.py (S-249)",
        "DepthFusion list projects: 3 projects registered (depthfusion, agent-ops, kitabu).",
        "Auto-learn session: 7 new facts extracted, 2 decisions recorded, 1 error trace added.",
        "Compress session: 14 chunks merged into 4 summary chunks. Compression ratio: 3.5x.",
        "Recommend model: for code-generation task — claude-sonnet-4-6 (Anthropic, subscription).",
        "Recall relevant: top-5 results for 'decay scoring agent memory' retrieved in 38ms.",
        "Pinned discovery: 'BACKLOG.md grep-only access pattern' — score=0.98, never evict.",
        "Set memory score: item_id=dec_fable5_001 score=0.97 (architectural decision, critical).",
        "Recall feedback recorded: thumbs_down for rank=3 result, relevance mismatch noted.",
        "Context bridge: DepthFusion MCP connected to Claude Code session via MCP protocol.",
        "Mark superseded: old_chunk_id=ctx_2026_06_15_001 superseded by ctx_2026_08_01_004.",
        "Ingest project: scanned 47 source files, 12 docs, 3 config files. 162 chunks ingested.",
        "Graph status: 2 orphaned nodes detected. Run `depthfusion_graph_traverse` to inspect.",
        "Query telemetry: avg recall@1=0.31 over last 100 queries. Embedding upgrade recommended.",
        "Session seed result: 8 relevant context items seeded into session from DepthFusion.",
        "Pin discovery: item_id=code_async_001 pinned. Will not be pruned regardless of age.",
        "Model performance: all-mpnet-base-v2 MRR=97.04% vs all-MiniLM-L6-v2 MRR=93.71%.",
        "Embedding dimension: all-MiniLM-L6-v2=384, all-mpnet-base-v2=768, bge-small-en=384.",
        "Benchmark seed 42: ensures reproducible corpus shuffling and query selection.",
        "Recall@1 baseline: 25.53% with all-MiniLM-L6-v2 on synthetic agent-memory corpus.",
        "NDCG@10 baseline: 77.47% with all-MiniLM-L6-v2; 78.92% with all-mpnet-base-v2.",
        "MRR uplift: +3.33pp from all-MiniLM-L6-v2 to all-mpnet-base-v2 on agent-memory queries.",
        "Embed latency: all-MiniLM-L6-v2=0.15s vs all-mpnet-base-v2=0.23s for 125-doc corpus.",
        "Gold relevance labels: category-level (all docs of a category) + item-specific (2 variants).",
        "Query types: 22 category-level queries + 28 specific-item queries = 50 total.",
        "RNG reproducibility: random.Random(42) + np.random.seed(42) for full determinism.",
        "Documentation standards: dual md+html with document-control block and version history.",
        "Output filename: DepthFusion_Embedding AB Benchmark_v1.0_<ddmmccyy>.md/.html",
        "Agent-memory categories: code (python functions, class defs), decisions (ADRs), errors, config.",
        "Paraphrase variant: each template gets a v2 with a prefix word (Note/Context/Recall/etc).",
        "Filler docs: benchmark-process items providing topical diversity without gold labels.",
        "No API key required: HuggingFace public models cached locally via sentence-transformers.",
        "Offline mode: set TRANSFORMERS_OFFLINE=1 after first download for air-gapped runs.",
        "Benchmark reproducibility confirmed: two runs with same seed produce identical results.",
    ]
    for j, text in enumerate(filler_texts):
        docs.append({"id": f"filler_{j}", "category": "filler", "text": text})

    rng.shuffle(docs)
    return docs


@dataclass
class Query:
    """A benchmark query with gold-relevant document IDs."""
    qid: str
    text: str
    relevant_ids: set[str] = field(default_factory=set)


def build_queries(corpus: list[dict], rng: random.Random) -> list[Query]:
    """Build ~50 queries with gold relevance labels from corpus."""
    queries: list[Query] = []

    # Category-level queries — all docs of that category are relevant
    category_queries = [
        ("q_code_decay", "function that applies exponential decay to a score based on age", "code"),
        ("q_code_async", "async request processing dispatched via executor", "code"),
        ("q_code_dedup", "deduplicate items against existing corpus using similarity threshold", "code"),
        ("q_code_embed", "batch embed list of texts with sentence transformers and normalize", "code"),
        ("q_code_cosine", "compute cosine similarity between two embedding vectors", "code"),
        ("q_code_stream", "async generator that streams events from an MCP session queue", "code"),
        ("q_code_auth", "require authenticated principal by validating JWT bearer token", "code"),
        ("q_decision_vendor", "vendor isolation rule for code review to prevent same-vendor grading", "decision"),
        ("q_decision_mcp", "MCP tools structure with thin wrappers delegating to impl functions", "decision"),
        ("q_decision_bind", "loopback-only network binding policy for datastores", "decision"),
        ("q_decision_docs", "durable vs ephemeral output document routing decision", "decision"),
        ("q_decision_backlog", "BACKLOG.md as authoritative source for epic status", "decision"),
        ("q_error_thrash", "autocompact thrashing caused by reading large BACKLOG file", "error"),
        ("q_error_auth", "403 forbidden error missing bearer token authentication", "error"),
        ("q_error_redis", "Redis WRONGTYPE error from stream key collision", "error"),
        ("q_error_pytest", "pytest asyncio timeout event loop not closed", "error"),
        ("q_error_module", "ModuleNotFoundError depthfusion not installed", "error"),
        ("q_config_port", "MCP server default port and host binding configuration", "config"),
        ("q_config_embed", "default embedding model configuration and upgrade path", "config"),
        ("q_config_decay", "decay half-life configuration environment variable", "config"),
        ("q_config_chroma", "ChromaDB collection name and path configuration", "config"),
        ("q_config_jwt", "JWT validation JWKS URL and audience configuration", "config"),
    ]

    category_map: dict[str, list[str]] = {}
    for doc in corpus:
        cat = doc["category"]
        if cat not in category_map:
            category_map[cat] = []
        category_map[cat].append(doc["id"])

    for qid, text, cat in category_queries:
        relevant = set(category_map.get(cat, []))
        if relevant:
            queries.append(Query(qid=qid, text=text, relevant_ids=relevant))

    # Specific-item queries — single highly-relevant doc
    specific_pairs = [
        ("q_specific_miniLM", "sentence transformer all-MiniLM-L6-v2 model instantiation",
         ["code_5", "code_5_v2"]),
        ("q_specific_recall_fn", "recall at k function implementation set intersection",
         ["code_6", "code_6_v2"]),
        ("q_specific_decay_halflife", "DEPTHFUSION_DECAY_HALFLIFE_DAYS default 30 days",
         ["config_4", "config_4_v2"]),
        ("q_specific_hnswlib", "hnswlib index parameters ef_construction M ef_search rebuild",
         ["config_13", "config_13_v2"]),
        ("q_specific_score_range", "memory score range pinned boost decay pruning threshold",
         ["config_12", "config_12_v2"]),
        ("q_specific_tauri", "Tauri app bundle identifier icon src-tauri",
         ["config_8", "config_8_v2"]),
        ("q_specific_structlog", "structlog JSON renderer ConsoleRenderer LOG_LEVEL",
         ["config_10", "config_10_v2"]),
        ("q_specific_twolocks", "two-lock model FeedbackStore per-file read global write",
         ["decision_8", "decision_8_v2"]),
        ("q_specific_graph_protocol", "GraphBackend Protocol mirrors StreamBackend extension pattern",
         ["decision_9", "decision_9_v2"]),
        ("q_specific_uvicorn_port", "uvicorn startup address already in use another instance",
         ["error_6", "error_6_v2"]),
        ("q_specific_chroma_missing", "ChromaDB collection not found run backfill_acl",
         ["error_4", "error_4_v2"]),
        ("q_specific_tauri_build", "Tauri build failed pnpm install frozen lockfile",
         ["error_11", "error_11_v2"]),
        ("q_specific_git_push", "git push rejected non-fast-forward rebase on main",
         ["error_9", "error_9_v2"]),
        ("q_specific_eventstore", "EventStore class constructor backend StreamBackend",
         ["code_10", "code_10_v2"]),
        ("q_specific_health", "FastAPI health endpoint unauthenticated route",
         ["code_12", "code_12_v2"]),
        ("q_specific_frontmatter", "atomic frontmatter rewrite tmp file replace path",
         ["code_13", "code_13_v2"]),
        ("q_specific_ci", "GitHub Actions CI matrix python 3.11 ubuntu cache poetry",
         ["config_7", "config_7_v2"]),
        ("q_specific_session_slug", "project slug max 64 chars lowercase alphanumeric hyphens",
         ["config_11", "config_11_v2"]),
        ("q_specific_conventional", "conventional commits feat fix chore docs 72 char subject",
         ["decision_10", "decision_10_v2"]),
        ("q_specific_extras", "optional dependency extras local vps-cpu vps-gpu CVE pins",
         ["decision_11", "decision_11_v2"]),
        ("q_specific_pinned", "pinned item score boost discovery timestamp",
         ["code_14", "code_14_v2"]),
        ("q_specific_offline", "sentence transformers offline mode TRANSFORMERS_OFFLINE cache",
         ["error_3", "error_3_v2"]),
        ("q_specific_max_confidence", "max per-entry confidence file-level importance not mean",
         ["decision_0", "decision_0_v2"]),
        ("q_specific_decay_fn", "exponential decay score age days 0.9 base",
         ["code_0", "code_0_v2"]),
        ("q_specific_redis_url", "Redis Streams backend URL REDIS_URL env var",
         ["config_4", "config_4_v2"]),
        ("q_specific_require_principal", "require principal JWT decode JWKS algorithms RS256",
         ["code_11", "code_11_v2"]),
        ("q_specific_oidc_expire", "jwt ExpiredSignatureError OIDC token re-authenticate",
         ["error_7", "error_7_v2"]),
        ("q_specific_candidates", "identify pruning candidates age max score threshold",
         ["code_4", "code_4_v2"]),
    ]

    doc_ids = {doc["id"] for doc in corpus}
    for qid, text, relevant_list in specific_pairs:
        # Only include IDs that actually exist in corpus
        relevant = {rid for rid in relevant_list if rid in doc_ids}
        if relevant:
            queries.append(Query(qid=qid, text=text, relevant_ids=relevant))

    rng.shuffle(queries)
    return queries[:50]  # cap at 50


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def recall_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """Recall@k — fraction of relevant docs retrieved in top-k."""
    if not relevant:
        return 0.0
    hits = len(set(ranked[:k]) & relevant)
    return hits / len(relevant)


def mean_reciprocal_rank(relevant: set[str], ranked: list[str]) -> float:
    """MRR — reciprocal rank of the first relevant document."""
    for rank, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(relevant: set[str], ranked: list[str], k: int = 10) -> float:
    """NDCG@k — normalized discounted cumulative gain (binary relevance)."""
    dcg = 0.0
    for rank, doc_id in enumerate(ranked[:k], start=1):
        if doc_id in relevant:
            dcg += 1.0 / math.log2(rank + 1)

    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass
class BenchmarkResults:
    model_name: str
    recall_1: float
    recall_3: float
    recall_5: float
    mrr: float
    ndcg: float
    num_queries: int
    num_docs: int
    embed_time_s: float


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    model_name: str,
    corpus: list[dict],
    queries: list[Query],
) -> BenchmarkResults:
    """Embed corpus + queries and compute retrieval metrics."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        print(f"ERROR: sentence-transformers not installed — {exc}", file=sys.stderr)
        sys.exit(1)

    import time

    print(f"  Loading model: {model_name} ...", flush=True)
    model = SentenceTransformer(model_name)

    corpus_texts = [doc["text"] for doc in corpus]
    corpus_ids = [doc["id"] for doc in corpus]

    print(f"  Embedding {len(corpus_texts)} corpus docs ...", flush=True)
    t0 = time.perf_counter()
    corpus_embeddings = model.encode(
        corpus_texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=64,
    )
    embed_time = time.perf_counter() - t0
    print(f"  Corpus embedded in {embed_time:.2f}s", flush=True)

    query_texts = [q.text for q in queries]
    query_embeddings = model.encode(
        query_texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=64,
    )

    corpus_matrix = np.array(corpus_embeddings)  # (N, D)
    query_matrix = np.array(query_embeddings)    # (Q, D)

    # Cosine similarity: since embeddings are L2-normalised, dot product == cosine
    scores_matrix = query_matrix @ corpus_matrix.T  # (Q, N)

    recall_1_scores = []
    recall_3_scores = []
    recall_5_scores = []
    mrr_scores = []
    ndcg_scores = []

    for qi, query in enumerate(queries):
        scores = scores_matrix[qi]
        ranked_indices = np.argsort(scores)[::-1]
        ranked_ids = [corpus_ids[i] for i in ranked_indices]

        recall_1_scores.append(recall_at_k(query.relevant_ids, ranked_ids, 1))
        recall_3_scores.append(recall_at_k(query.relevant_ids, ranked_ids, 3))
        recall_5_scores.append(recall_at_k(query.relevant_ids, ranked_ids, 5))
        mrr_scores.append(mean_reciprocal_rank(query.relevant_ids, ranked_ids))
        ndcg_scores.append(ndcg_at_k(query.relevant_ids, ranked_ids, k=10))

    return BenchmarkResults(
        model_name=model_name,
        recall_1=float(np.mean(recall_1_scores)),
        recall_3=float(np.mean(recall_3_scores)),
        recall_5=float(np.mean(recall_5_scores)),
        mrr=float(np.mean(mrr_scores)),
        ndcg=float(np.mean(ndcg_scores)),
        num_queries=len(queries),
        num_docs=len(corpus),
        embed_time_s=embed_time,
    )


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

def _fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def _delta(a: float, b: float) -> str:
    diff = (b - a) * 100
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.2f}pp"


def write_markdown(
    results_a: BenchmarkResults,
    results_b: BenchmarkResults,
    output_dir: Path,
    date_str: str,
) -> Path:
    filename = f"DepthFusion_Embedding AB Benchmark_v1.0_{date_str}.md"
    path = output_dir / filename

    lines = [
        "---",
        "**Document Control**",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Project | DepthFusion |",
        "| Document | Embedding A/B Benchmark |",
        "| Author | Greg Morris |",
        "| Version | v1.0 |",
        f"| Date | {date_str[:2]}-{date_str[2:4]}-{date_str[4:]} |",
        "| Status | Draft |",
        "---",
        "",
        "# DepthFusion — Embedding A/B Benchmark",
        "",
        "## Overview",
        "",
        "Standalone embedding model comparison using a deterministic synthetic corpus",
        "of agent-memory-style items (code snippets, decisions, error traces, config facts).",
        f"Corpus: **{results_a.num_docs} documents**, Queries: **{results_a.num_queries}**.",
        "No API key required — sentence-transformers local models only. (S-249)",
        "",
        "## Corpus & Query Design",
        "",
        "- ~200 documents across 4 categories: code snippets, decisions, error traces, config facts",
        "- ~50 queries with gold-relevance labels (category-level and item-specific)",
        "- Deterministic RNG seed: 42 — fully reproducible, no external data dependency",
        "",
        "## Results",
        "",
        "| Metric | Model A | Model B | Delta (B−A) |",
        "|---|---|---|---|",
        f"| **Model** | `{results_a.model_name}` | `{results_b.model_name}` | — |",
        f"| Recall@1 | {_fmt_pct(results_a.recall_1)} | {_fmt_pct(results_b.recall_1)} | {_delta(results_a.recall_1, results_b.recall_1)} |",
        f"| Recall@3 | {_fmt_pct(results_a.recall_3)} | {_fmt_pct(results_b.recall_3)} | {_delta(results_a.recall_3, results_b.recall_3)} |",
        f"| Recall@5 | {_fmt_pct(results_a.recall_5)} | {_fmt_pct(results_b.recall_5)} | {_delta(results_a.recall_5, results_b.recall_5)} |",
        f"| MRR | {_fmt_pct(results_a.mrr)} | {_fmt_pct(results_b.mrr)} | {_delta(results_a.mrr, results_b.mrr)} |",
        f"| NDCG@10 | {_fmt_pct(results_a.ndcg)} | {_fmt_pct(results_b.ndcg)} | {_delta(results_a.ndcg, results_b.ndcg)} |",
        f"| Embed time (corpus) | {results_a.embed_time_s:.2f}s | {results_b.embed_time_s:.2f}s | — |",
        "",
        "## Interpretation",
        "",
        "- **Recall@k**: fraction of relevant documents retrieved in top-k (higher is better)",
        "- **MRR**: reciprocal rank of the first relevant hit (higher is better)",
        "- **NDCG@10**: normalized discounted cumulative gain at depth 10 (higher is better)",
        "- **pp** = percentage points absolute difference",
        "",
        "## Reproduction",
        "",
        "```bash",
        f"python scripts/embedding_ab_benchmark.py \\",
        f"    --model-a {results_a.model_name} \\",
        f"    --model-b {results_b.model_name}",
        "```",
        "",
        "---",
        "",
        "## Version History",
        "",
        "| Version | Date | Author | Changes |",
        "|---|---|---|---|",
        f"| v1.0 | {date_str[:2]}-{date_str[2:4]}-{date_str[4:]} | Greg Morris | Initial release |",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_html(
    results_a: BenchmarkResults,
    results_b: BenchmarkResults,
    output_dir: Path,
    date_str: str,
) -> Path:
    filename = f"DepthFusion_Embedding AB Benchmark_v1.0_{date_str}.html"
    path = output_dir / filename
    disp_date = f"{date_str[:2]}-{date_str[2:4]}-{date_str[4:]}"

    def row(label: str, va: float, vb: float) -> str:
        delta = _delta(va, vb)
        color = "#2d7a2d" if (vb - va) >= 0 else "#c0392b"
        return (
            f"<tr><td>{label}</td>"
            f"<td>{_fmt_pct(va)}</td>"
            f"<td>{_fmt_pct(vb)}</td>"
            f"<td style='color:{color};font-weight:bold'>{delta}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Embedding A/B Benchmark — DepthFusion</title>
  <style>
    body {{ font-family: Georgia, serif; max-width: 860px; margin: 40px auto;
           padding: 0 24px; line-height: 1.7; color: #222; background: #fff; }}
    h1 {{ font-size: 1.6rem; border-bottom: 2px solid #333; padding-bottom: 8px; }}
    h2 {{ font-size: 1.2rem; margin-top: 2em; border-bottom: 1px solid #ccc; }}
    h3 {{ font-size: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
    th, td {{ border: 1px solid #ccc; padding: 8px 12px; text-align: left; }}
    th {{ background: #f4f4f4; font-weight: bold; }}
    .doc-control {{ background: #f9f9f9; border: 1px solid #ddd;
                   border-radius: 4px; padding: 12px; margin-bottom: 2em; }}
    .doc-control table {{ margin: 0; }}
    .doc-control th {{ width: 120px; }}
    code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px;
           font-family: monospace; font-size: 0.9em; }}
    pre {{ background: #f0f0f0; padding: 16px; border-radius: 4px;
          overflow-x: auto; font-size: 0.9em; }}
    hr {{ border: none; border-top: 1px solid #ddd; margin: 2em 0; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #1a1a1a; color: #e0e0e0; }}
      th {{ background: #2a2a2a; }} td, th {{ border-color: #444; }}
      .doc-control {{ background: #222; border-color: #444; }}
      code, pre {{ background: #2a2a2a; }}
    }}
  </style>
</head>
<body>

<h1>DepthFusion — Embedding A/B Benchmark</h1>

<div class="doc-control">
  <table>
    <tr><th>Project</th><td>DepthFusion</td></tr>
    <tr><th>Document</th><td>Embedding A/B Benchmark</td></tr>
    <tr><th>Author</th><td>Greg Morris</td></tr>
    <tr><th>Version</th><td>v1.0</td></tr>
    <tr><th>Date</th><td>{disp_date}</td></tr>
    <tr><th>Status</th><td>Draft</td></tr>
  </table>
</div>

<h2>Overview</h2>
<p>Standalone embedding model comparison using a deterministic synthetic corpus
of agent-memory-style items (code snippets, decisions, error traces, config facts).
Corpus: <strong>{results_a.num_docs} documents</strong>,
Queries: <strong>{results_a.num_queries}</strong>.
No API key required — sentence-transformers local models only. (S-249)</p>

<h2>Corpus &amp; Query Design</h2>
<ul>
  <li>~200 documents across 4 categories: code snippets, decisions, error traces, config facts</li>
  <li>~50 queries with gold-relevance labels (category-level and item-specific)</li>
  <li>Deterministic RNG seed: 42 — fully reproducible, no external data dependency</li>
</ul>

<h2>Results</h2>
<table>
  <thead>
    <tr>
      <th>Metric</th>
      <th>Model A<br><code>{results_a.model_name}</code></th>
      <th>Model B<br><code>{results_b.model_name}</code></th>
      <th>Delta (B−A)</th>
    </tr>
  </thead>
  <tbody>
    {row("Recall@1", results_a.recall_1, results_b.recall_1)}
    {row("Recall@3", results_a.recall_3, results_b.recall_3)}
    {row("Recall@5", results_a.recall_5, results_b.recall_5)}
    {row("MRR", results_a.mrr, results_b.mrr)}
    {row("NDCG@10", results_a.ndcg, results_b.ndcg)}
    <tr>
      <td>Embed time (corpus)</td>
      <td>{results_a.embed_time_s:.2f}s</td>
      <td>{results_b.embed_time_s:.2f}s</td>
      <td>—</td>
    </tr>
  </tbody>
</table>

<h2>Metric Definitions</h2>
<ul>
  <li><strong>Recall@k</strong>: fraction of relevant documents retrieved in top-k (higher is better)</li>
  <li><strong>MRR</strong>: reciprocal rank of the first relevant hit (higher is better)</li>
  <li><strong>NDCG@10</strong>: normalized discounted cumulative gain at depth 10 (higher is better)</li>
  <li><strong>pp</strong> = percentage points absolute difference</li>
</ul>

<h2>Reproduction</h2>
<pre>python scripts/embedding_ab_benchmark.py \\
    --model-a {results_a.model_name} \\
    --model-b {results_b.model_name}</pre>

<hr>
<h2>Version History</h2>
<table>
  <thead><tr><th>Version</th><th>Date</th><th>Author</th><th>Changes</th></tr></thead>
  <tbody>
    <tr><td>v1.0</td><td>{disp_date}</td><td>Greg Morris</td><td>Initial release</td></tr>
  </tbody>
</table>

</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Embedding A/B Benchmark — compare two sentence-transformers models "
            "on a deterministic synthetic agent-memory corpus. (S-249)"
        )
    )
    parser.add_argument(
        "--model-a",
        default="all-MiniLM-L6-v2",
        help="Model A name (sentence-transformers). Default: all-MiniLM-L6-v2",
    )
    parser.add_argument(
        "--model-b",
        default="all-mpnet-base-v2",
        help="Model B name (sentence-transformers). Default: all-mpnet-base-v2",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/agent-outputs",
        help="Directory to write benchmark reports (md + html). Default: docs/agent-outputs",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=_SEED,
        help=f"RNG seed for corpus/query generation. Default: {_SEED}",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing output files (useful for quick local checks).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("DepthFusion — Embedding A/B Benchmark (S-249)")
    print("=" * 60)
    print(f"Model A : {args.model_a}")
    print(f"Model B : {args.model_b}")
    print(f"Seed    : {args.seed}")
    print()

    rng = random.Random(args.seed)
    np.random.seed(args.seed)

    print("[1/4] Building synthetic corpus ...")
    corpus = build_corpus(rng)
    print(f"      Corpus size: {len(corpus)} documents")

    print("[2/4] Building queries with gold labels ...")
    queries = build_queries(corpus, rng)
    print(f"      Queries: {len(queries)}")

    print()
    print("[3/4] Running benchmark for Model A ...")
    results_a = run_benchmark(args.model_a, corpus, queries)

    print()
    print("[4/4] Running benchmark for Model B ...")
    results_b = run_benchmark(args.model_b, corpus, queries)

    # Print results table
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    header = f"{'Metric':<14} {'Model A':>12} {'Model B':>12} {'Delta (B-A)':>14}"
    print(header)
    print("-" * 56)
    metrics = [
        ("Recall@1",  results_a.recall_1, results_b.recall_1),
        ("Recall@3",  results_a.recall_3, results_b.recall_3),
        ("Recall@5",  results_a.recall_5, results_b.recall_5),
        ("MRR",       results_a.mrr,      results_b.mrr),
        ("NDCG@10",   results_a.ndcg,     results_b.ndcg),
    ]
    for name, va, vb in metrics:
        delta = (vb - va) * 100
        sign = "+" if delta >= 0 else ""
        print(f"{name:<14} {_fmt_pct(va):>12} {_fmt_pct(vb):>12} {sign}{delta:>+11.2f}pp")
    print("-" * 56)
    print(f"{'Model A':<14} {args.model_a}")
    print(f"{'Model B':<14} {args.model_b}")
    print(f"{'Docs':<14} {len(corpus)}")
    print(f"{'Queries':<14} {len(queries)}")
    print(f"{'Embed A':<14} {results_a.embed_time_s:.2f}s")
    print(f"{'Embed B':<14} {results_b.embed_time_s:.2f}s")
    print("=" * 60)

    if not args.no_write:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.date.today().strftime("%d%m%Y")

        md_path = write_markdown(results_a, results_b, output_dir, date_str)
        html_path = write_html(results_a, results_b, output_dir, date_str)

        print()
        print("Reports written:")
        print(f"  {md_path}")
        print(f"  {html_path}")


if __name__ == "__main__":
    main()
