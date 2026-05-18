"""Recall pipeline — orchestrates BM25 + optional haiku reranker + optional ChromaDB.

PipelineMode.LOCAL:       BM25 only, no API calls
PipelineMode.VPS_TIER1:   BM25 top-10 -> HaikuReranker -> top-k
PipelineMode.VPS_TIER2:   ChromaDB top-20 + BM25 top-10 -> RRF fusion -> HaikuReranker -> top-k

v0.5.0 T-130: `apply_vector_search()` computes cosine similarity using the
embedding backend from `get_backend("embedding")` (LocalEmbeddingBackend
on vps-gpu, NullBackend elsewhere). Its output is a ranked block list
suitable for RRF fusion with BM25 results via the existing `rrf_fuse()`.

v0.5.0 T-160: project-scoped recall filter.
`extract_frontmatter_project()` parses `project:` YAML frontmatter from a
block's content. `filter_blocks_by_project()` keeps only blocks whose
project matches the current project (or have no frontmatter, for back-compat).

v0.5.0 T-157: selective fusion gates (Mamba B/C/Δ port, S-51).
`apply_fusion_gates()` runs the three-stage gate after BM25 + RRF fusion
and before reranking. Gated on `DEPTHFUSION_FUSION_GATES_ENABLED=true`
(default false — preserves v0.4.x byte-identity). Emits a D-3-compliant
gate log per query via MetricsCollector.

v1.0.0 T-323: CognitiveScorer integration (E-31 Structured Evolving Cognition).
`apply_cognitive_scoring()` re-scores result blocks using `CognitiveScorer`
after the BM25/RRF/reranker pipeline. Gated on
`DEPTHFUSION_COGNITIVE_SCORING=true` (default false — preserves v0.6.x
byte-identity). Uses block's existing BM25 `score` and `vector_score` as
lexical/semantic inputs; all other ScoringContext fields default to
conservative values. Sorts results by cognitive score descending and
attaches `cognitive_score` to each returned block.
"""
from __future__ import annotations

import logging
import math
import os
import re
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from depthfusion.core.hit_tracker import HitTracker

from depthfusion.retrieval.reranker import HaikuReranker

logger = logging.getLogger(__name__)

# Session lifecycle boilerplate patterns — these lines carry metadata but no recall value.
# Short blocks consisting almost entirely of these markers are heavily penalised in BM25
# scoring so that content-rich blocks from the same session corpus rank above them.
_BOILERPLATE_LINE_RE = re.compile(
    r"^---+ (?:SESSION (?:START|END)|COMPACTION EVENT) at \d",
    re.MULTILINE,
)

# Lexical richness penalty constants — used by lexical_richness_penalty().
_WORD_RE = re.compile(r"[a-zA-Z]{2,}")
_RICHNESS_MIN_TOKENS: int = 20
_TTR_FLOOR: float = 0.20

# Project slug embedded in session event content by DepthFusion session-start/end hooks.
# Format (in session capture files): "Project: <slug>" on its own line.
# This is distinct from YAML frontmatter — session files use plain-text headers.
_SESSION_PROJECT_RE = re.compile(r"^Project:\s+(\S+)\s*$", re.MULTILINE)

# Frontmatter pattern — same shape as capture/dedup.py uses for discovery files.
# Duplicated deliberately: retrieval/hybrid.py is on the recall hot path and
# capture/dedup.py runs under the git post-commit hook; keeping them decoupled
# means importing one never transitively loads the other's heavy deps.
#
# Bounded to the opening `---\n...\n---` block so that prose in the body
# (e.g. a code snippet that happens to contain `project: other`) cannot
# override the real frontmatter tag. Uses non-greedy `.*?` with DOTALL so
# the closing `---` is matched on its own line.
_FRONTMATTER_PROJECT_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n", re.DOTALL,
)
_FRONTMATTER_PROJECT_KEY_RE = re.compile(
    r"^project:\s*(\S+)\s*$", re.MULTILINE,
)

try:
    from depthfusion.storage.tier_manager import Tier as _StorageTier
    from depthfusion.storage.tier_manager import TierManager as _TierManager
    _TIER_MANAGER_AVAILABLE = True
except ImportError:
    # Sentinel bindings when the storage extras aren't installed. Using
    # distinct *private* names (_TierManager / _StorageTier) and assigning
    # None to them avoids the "Cannot assign to a type" mypy error that
    # occurs when the import-aliased name shadows a type symbol.
    _TierManager = None  # type: ignore[misc,assignment]
    _StorageTier = None  # type: ignore[misc,assignment]
    _TIER_MANAGER_AVAILABLE = False

# Public module-level name preserved for back-compat with existing callers
# and test monkeypatches that reference `depthfusion.retrieval.hybrid.TierManager`.
TierManager = _TierManager


class PipelineMode(Enum):
    LOCAL = "local"
    VPS_TIER1 = "vps-tier1"
    VPS_TIER2 = "vps-tier2"


class RecallPipeline:
    """Configures the retrieval pipeline based on install mode and tier."""

    def __init__(self, mode: PipelineMode = PipelineMode.LOCAL):
        self.mode = mode
        self._reranker = HaikuReranker() if mode != PipelineMode.LOCAL else None

    @classmethod
    def from_env(cls) -> "RecallPipeline":
        """Build pipeline from environment variables.

        Reads DEPTHFUSION_MODE (local|vps-cpu|vps-gpu|mac-mlx; 'vps' is a
        deprecated alias for 'vps-cpu') and queries TierManager when in
        vps-cpu/vps-gpu mode.  mac-mlx uses VPS_TIER1 (BM25 + Haiku reranker)
        with its local LLM backend.  Falls back to VPS_TIER1 if TierManager is
        unavailable.
        """
        from depthfusion.utils.mode import normalise_mode
        install_mode = normalise_mode(os.environ.get("DEPTHFUSION_MODE"))
        if install_mode == "local":
            return cls(mode=PipelineMode.LOCAL)
        if install_mode == "mac-mlx":
            return cls(mode=PipelineMode.VPS_TIER1)
        if not _TIER_MANAGER_AVAILABLE or TierManager is None:
            return cls(mode=PipelineMode.VPS_TIER1)
        try:
            tm = TierManager()
            cfg = tm.detect_tier()
            if _StorageTier is not None and cfg.tier == _StorageTier.VPS_TIER2:
                return cls(mode=PipelineMode.VPS_TIER2)
            return cls(mode=PipelineMode.VPS_TIER1)
        except Exception:
            return cls(mode=PipelineMode.VPS_TIER1)

    def apply_reranker(
        self, blocks: list[dict], query: str, top_k: int = 5
    ) -> list[dict]:
        """Apply the reranker if available; otherwise return top_k of BM25 order."""
        if self.mode == PipelineMode.LOCAL or self._reranker is None:
            return blocks[:top_k]
        return self._reranker.rerank(query, blocks, top_k=top_k)

    def maybe_expand_query(
        self,
        query: str,
        graph_store: "Any | None" = None,
    ) -> str:
        """Expand query with graph-linked terms when DEPTHFUSION_GRAPH_ENABLED=true.

        Returns original query unchanged if:
        - DEPTHFUSION_GRAPH_ENABLED is not 'true'
        - graph_store is None
        - graph has 0 nodes
        """
        if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() != "true":
            return query
        if graph_store is None:
            return query
        from depthfusion.graph.traverser import expand_query  # ImportError is intentionally loud
        try:
            if graph_store.node_count() == 0:
                return query
            return expand_query(query, graph_store)
        except Exception:
            return query

    def apply_fusion_gates(
        self,
        blocks: list[dict],
        *,
        query: str = "",
        query_embedding: list[float] | None = None,
        mode_label: str = "",
    ) -> list[dict]:
        """Run the selective fusion gates (Mamba B/C/Δ) over `blocks`.

        T-157 / S-51: gated on `DEPTHFUSION_FUSION_GATES_ENABLED=true`.
        When disabled (default), returns `blocks` unchanged without
        running gates — preserves v0.4.x byte-identity of the recall path.

        Emits a D-3-compliant gate log via MetricsCollector whether or not
        any block is rejected — observability first, optimization second.

        Contract:
          - Fail-open on any internal error: return the original `blocks`
            so gate bugs never degrade recall quality below baseline.
          - `query_embedding` is optional; absent → B/C gates fall back to
            BM25-percentile and score-proximity heuristics respectively.
          - The returned list is sorted by `gate_fused_score` desc when
            gates run; by original order when gates are disabled.
        """
        if os.environ.get("DEPTHFUSION_FUSION_GATES_ENABLED", "false").lower() not in (
            "true", "1", "yes",
        ):
            return blocks
        if not blocks:
            return blocks

        try:
            from depthfusion.fusion.gates import GateConfig, SelectiveFusionGates
            cfg = GateConfig.from_env()
            gates = SelectiveFusionGates(config=cfg)
            survivors, log = gates.apply(blocks, query_embedding=query_embedding)
            # Deterministic snapshot ID of the config used for this decision
            # (S-58 / I-8 compliance; closes the TODO marker from S-51).
            config_version_id = cfg.version_id()
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            logger.debug("apply_fusion_gates: degraded to pass-through (%s)", exc)
            return blocks

        # Defensive fallback flag: set now so the gate log reflects whether
        # the retrieval layer overrode the gate verdict (see below).
        fallback_triggered = not survivors

        # Emit gate log (D-3 invariant). Swallow any metrics failure so
        # observability never degrades retrieval.
        # I-8 compliance (S-58): `config_version_id` is a deterministic
        # hash of the active GateConfig — auditors can reproduce any gate
        # decision against the exact config that produced it. Per DR-018
        # §4 ratification and docs/plans/v0.5/03-skillforge-integration.md
        # §3.3.5, this field is mandatory on every gate-log record.
        try:
            import hashlib

            from depthfusion.metrics.collector import MetricsCollector
            query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12] if query else ""
            MetricsCollector().record_gate_log(
                log,
                query_hash=query_hash,
                mode=mode_label or self.mode.value,
                config_version_id=config_version_id,
                fallback_triggered=fallback_triggered,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("apply_fusion_gates: gate-log emission failed (%s)", exc)

        # Defensive: if gates filtered everything, fall back to the original
        # pool rather than returning nothing (recall correctness > gate signal).
        if not survivors:
            logger.info(
                "Fusion gates rejected all %d candidates — falling back to original pool",
                len(blocks),
            )
            return blocks
        return survivors

    def apply_cognitive_scoring(
        self,
        blocks: list[dict],
    ) -> list[dict]:
        """Re-score `blocks` using CognitiveScorer when the feature flag is on.

        T-323 / E-31: gated on `DEPTHFUSION_COGNITIVE_SCORING=true`.
        When disabled (default), returns `blocks` unchanged — preserves
        v0.6.x byte-identity of the recall path.

        Scoring context mapping from block fields:
          - `lexical`  = block['score'] normalised to [0, 1] across the batch
          - `semantic` = block['vector_score'] if present, else 0.0
          - `recency`  = block['recency'] if present, else 0.5 (neutral)
          - all other ScoringContext fields = their dataclass defaults
            (conservative: confidence=0.7, everything else 0.0)

        Attaches `cognitive_score` to each block and returns the list
        sorted by `cognitive_score` descending. Fail-open: if CognitiveScorer
        raises, returns the original `blocks` unchanged.
        """
        if os.getenv("DEPTHFUSION_COGNITIVE_SCORING", "false").lower() != "true":
            return blocks
        if not blocks:
            return blocks

        try:
            from depthfusion.cognitive.scorer import CognitiveScorer, ScoringContext

            scorer = CognitiveScorer()

            # Normalise raw BM25 scores to [0, 1] using min-max so that
            # the range of the batch maps to [0, 1] regardless of absolute
            # values.  When all scores are equal (score_range == 0) we
            # assign the neutral midpoint 0.5 to avoid biasing the scorer.
            raw_scores = [float(b.get("score", 0.0)) for b in blocks]
            min_score = min(raw_scores)
            max_score = max(raw_scores)
            score_range = max_score - min_score

            scored: list[tuple[float, dict]] = []
            for block, raw in zip(blocks, raw_scores, strict=False):
                lexical = (raw - min_score) / score_range if score_range > 0.0 else 0.5
                semantic = float(block.get("vector_score", 0.0))
                recency = float(block.get("recency", 0.5))

                ctx = ScoringContext(
                    semantic=semantic,
                    lexical=lexical,
                    recency=recency,
                )
                cog_score = scorer.score(ctx)
                enriched = {**block, "cognitive_score": cog_score}
                scored.append((cog_score, enriched))

            scored.sort(key=lambda t: -t[0])
            result = [b for _, b in scored]

            logger.debug(
                "apply_cognitive_scoring: re-scored %d blocks (top cognitive_score=%.4f)",
                len(result),
                result[0]["cognitive_score"] if result else 0.0,
            )
            return result

        except Exception as exc:  # noqa: BLE001 — fail-open contract
            logger.debug("apply_cognitive_scoring: degraded to pass-through (%s)", exc)
            return blocks

    def apply_vector_search(
        self,
        query: str,
        blocks: list[dict],
        *,
        top_k: int = 10,
        backend: Any = None,
    ) -> list[dict]:
        """Rank `blocks` by cosine similarity between `query` and `block['snippet']`.

        T-130: uses `get_backend("embedding")` (LocalEmbeddingBackend on
        vps-gpu mode, NullBackend elsewhere). When the backend returns
        `None` (no sentence-transformers, load failure, or NullBackend),
        this method returns an empty list — callers fuse with BM25 via
        `rrf_fuse()`, where an empty vector list is a no-op.

        Contract:
          - Requires each block to have a 'snippet' key (string content).
          - Returns a NEW list of blocks sorted by descending cos-sim.
          - Each returned block has a 'vector_score' key added.
          - Top-k is applied AFTER sorting.
          - Never raises — embedding failures return []; the pipeline
            degrades gracefully to BM25-only.
        """
        if not blocks:
            return []

        if backend is None:
            try:
                from depthfusion.backends.factory import get_backend
                backend = get_backend("embedding")
            except Exception as exc:  # noqa: BLE001
                logger.debug("apply_vector_search: backend resolution failed: %s", exc)
                return []

        # Embed query + all block snippets in a single batched call.
        snippets = [str(b.get("snippet", "")) for b in blocks]
        texts = [query] + snippets
        try:
            embeddings = backend.embed(texts)
        except Exception as exc:  # noqa: BLE001
            logger.debug("apply_vector_search: embed() raised: %s", exc)
            return []

        if embeddings is None or len(embeddings) != len(texts):
            return []

        query_vec = embeddings[0]
        block_vecs = embeddings[1:]

        scored: list[tuple[float, dict]] = []
        for block, vec in zip(blocks, block_vecs, strict=False):
            score = _cosine_similarity(query_vec, vec)
            enriched = {**block, "vector_score": score}
            scored.append((score, enriched))

        scored.sort(key=lambda t: -t[0])
        return [b for _, b in scored[:top_k]]

    def rrf_fuse(
        self,
        bm25_results: list[dict],
        vector_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """Reciprocal Rank Fusion of two ranked lists.

        Both lists must have a 'chunk_id' key. Returns deduplicated, fused list.
        RRF score = sum(1 / (k + rank)) across all lists where the doc appears.
        """
        if not vector_results:
            return bm25_results
        if not bm25_results:
            return vector_results

        scores: dict[str, float] = {}
        all_blocks: dict[str, dict] = {}

        for rank, block in enumerate(bm25_results, start=1):
            cid = block["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            all_blocks[cid] = block

        for rank, block in enumerate(vector_results, start=1):
            cid = block["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            all_blocks[cid] = block

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [all_blocks[cid] for cid, _ in ranked]


def index_pass(raw_blocks: list[dict], top_k: int = 20) -> list[dict]:
    """S-113 mode='index': lightweight title+source entries, no BM25 scoring.

    Deduplicates by file_stem — one entry per source file. Each entry has:
    chunk_id, title (≤80 chars), source, tags, and timestamp when available.
    Token cost is ~10% of a full scored response for the same corpus.
    """
    results: list[dict] = []
    seen_files: set[str] = set()
    for block in raw_blocks:
        file_stem = block.get("file_stem", block["chunk_id"])
        if file_stem in seen_files:
            continue
        seen_files.add(file_stem)
        raw_title = block.get("title") or block["content"][:80].replace("\n", " ").strip()
        entry: dict = {
            "chunk_id": block["chunk_id"],
            "title": raw_title[:80],
            "source": block["source"],
            "tags": block.get("tags") or [],
        }
        if "mtime_iso" in block:
            entry["timestamp"] = block["mtime_iso"]
        results.append(entry)
        if len(results) >= top_k:
            break
    return results


def timeline_pass(raw_blocks: list[dict], top_k: int = 20) -> list[dict]:
    """S-113 mode='timeline': all blocks in recency order, no scoring, no dedup.

    Blocks arrive already sorted mtime-desc from the file-loading loop, so
    insertion order IS recency order. Includes all blocks — ambient items with
    low importance are not filtered out.
    """
    results: list[dict] = []
    for block in raw_blocks:
        raw_title = block.get("title") or block["content"][:80].replace("\n", " ").strip()
        entry: dict = {
            "chunk_id": block["chunk_id"],
            "title": raw_title[:80],
            "source": block["source"],
            "tags": block.get("tags") or [],
        }
        if "mtime_iso" in block:
            entry["timestamp"] = block["mtime_iso"]
        results.append(entry)
        if len(results) >= top_k:
            break
    return results


def fts_prefilter_memory_ids(
    store: "Any",
    query: str,
    *,
    limit: int = 50,
) -> list[str] | None:
    """Return FTS5-ranked memory IDs when `DEPTHFUSION_FTS_ENABLED=true`.

    T-389 / S-114: wraps `store._fts_search()` with the feature flag gate so
    callers only need to check the return value:
      - ``None``  → flag is off or FTS unavailable; fall through to full scan
      - ``[]``    → FTS ran but found no matches for `query`; caller may skip BM25
      - ``[...]`` → pre-filtered candidate IDs, sorted by FTS5 rank

    The caller is responsible for loading only the returned IDs from the store
    and then scoring them with BM25 / CognitiveScorer as usual.
    """
    if os.getenv("DEPTHFUSION_FTS_ENABLED", "true").lower() not in ("true", "1", "yes"):
        return None
    try:
        return store._fts_search(query, limit=limit)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — fail-open: never degrade recall quality
        logger.debug("fts_prefilter_memory_ids: degraded to full scan (%s)", exc)
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1.0, 1.0]; returns 0.0 for zero-vectors or
    length-mismatched inputs (rather than raising — the retrieval path
    must never hard-fail on degenerate embeddings).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# T-160 — Project-scoped recall filter
# ---------------------------------------------------------------------------

def extract_frontmatter_project(content: str) -> str | None:
    """Parse `project:` from YAML frontmatter; return the slug or None.

    Accepts discoveries that were written by any DepthFusion capture path
    (decision_extractor, negative_extractor, git_post_commit). The
    frontmatter block format is:
        ---
        project: <slug>
        ...
        ---

    Files without a `project:` key return None — callers treat None as
    "unknown project" and apply backward-compat rules (include in results
    regardless of filter, per S-52 AC-3).
    """
    if not content:
        return None
    # Restrict to the opening frontmatter block; prose in the body is ignored.
    fm_match = _FRONTMATTER_PROJECT_RE.match(content)
    if not fm_match:
        return None
    key_match = _FRONTMATTER_PROJECT_KEY_RE.search(fm_match.group("fm"))
    return key_match.group(1).strip() if key_match else None


def filter_blocks_by_project(
    blocks: list[dict],
    *,
    current_project: str | None,
    cross_project: bool = False,
    extra_projects: "frozenset[str] | None" = None,
) -> list[dict]:
    """Filter out blocks whose project tag names a different project.

    Project resolution order for each block:
      1. `block["project"]` — an explicit project key (preferred; set at
         file-load time by the caller, so it survives section-splitting
         that would strip the file-level frontmatter from later blocks)
      2. `extract_frontmatter_project(block["content"])` — parses any
         frontmatter that did survive inside the block's own content

    Rules (S-52 AC-1 / AC-2 / AC-3):
      * `cross_project=True`           → return `blocks` unchanged (v0.4.x behaviour)
      * `current_project` is None       → return `blocks` unchanged (no project
        context to filter against — e.g. recall outside any git repo)
      * Block has no project at all     → INCLUDED (back-compat for
        pre-v0.5 discoveries and user-written memory files)
      * Block project matches           → INCLUDED
      * Block project in extra_projects → INCLUDED (query-mention widening)
      * Block project differs           → EXCLUDED

    `extra_projects`: additional project slugs to admit beyond `current_project`.
    Used when the query explicitly names another project (e.g. "I'm working on
    the SkillForge router" from a depthfusion session) so that cross-project
    recall is widened precisely rather than globally.
    """
    if cross_project or current_project is None:
        return blocks

    allowed: set[str] = {current_project}
    if extra_projects:
        allowed |= extra_projects

    filtered: list[dict] = []
    for block in blocks:
        # Preferred: project tag attached at file-load time.
        project = block.get("project") or None
        # Fallback: try to parse frontmatter from the block's own content
        # (works for block 0 of a section-split file; later blocks have
        # already lost the frontmatter, which is why (1) above is preferred).
        if project is None:
            content = block.get("content", "")
            project = extract_frontmatter_project(content) if content else None
        if project is None or project in allowed:
            filtered.append(block)
    return filtered


def boilerplate_penalty(content: str) -> float:
    """Return 0.2 when content is predominantly session lifecycle boilerplate.

    Session files captured by DepthFusion hooks often begin (or consist entirely
    of) event envelopes — ``--- SESSION START/END/COMPACTION EVENT ---`` headers
    and their JSON payloads. Blocks that are almost entirely such envelopes score
    equally to content-rich sessions on BM25 because they share the same project
    name tokens.

    Threshold: any boilerplate marker found AND total non-empty lines ≤ 12.
    Longer blocks typically contain real content mixed with the envelope; short
    blocks with a boilerplate marker are almost always pure envelopes.
    """
    if not content:
        return 1.0
    if not _BOILERPLATE_LINE_RE.search(content):
        return 1.0
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return 0.2 if len(lines) <= 12 else 1.0


def lexical_richness_penalty(content: str) -> float:
    """Return a penalty factor in [0.5, 1.0] based on vocabulary diversity.

    Penalises content whose type-token ratio (unique_tokens / total_tokens)
    falls below TTR_FLOOR (0.20). Repetitive content — log dumps, template
    files, boilerplate prose — has a TTR near 0; high-information technical
    sessions have TTR >= 0.25.

    Very short content (<= RICHNESS_MIN_TOKENS = 20 word-tokens) returns 1.0
    to avoid false penalties on tightly scoped notes.
    """
    if not content:
        return 1.0
    tokens = [t.lower() for t in _WORD_RE.findall(content)]
    if len(tokens) <= _RICHNESS_MIN_TOKENS:
        return 1.0
    ttr = len(set(tokens)) / len(tokens)
    return max(0.5, min(1.0, ttr / _TTR_FLOOR))


_BOOST_PER_HIT: float = 0.1
_MAX_HITS_BOOST: float = 1.5


def query_hits_boost(chunk_id: str, tracker: "HitTracker | None" = None) -> float:
    """Return a boost multiplier [1.0, 1.5] based on 30-day retrieval hit count.

    Chunks retrieved and used in recent queries receive a persistent rank
    boost (up to 1.5×). Implements the query-feedback loop from OpenHuman's
    entity-hotness formula (2.0×query_hits term, adapted for DepthFusion).

    Returns 1.0 when tracker is None — no-op for configs without hit tracking.
    """
    if tracker is None:
        return 1.0
    hits = tracker.get_hits_30d(chunk_id)
    return min(1.0 + _BOOST_PER_HIT * hits, _MAX_HITS_BOOST)


def extract_session_project(content: str) -> str | None:
    """Parse the project slug from session event content.

    Session capture hooks embed a ``Project: <slug>`` line immediately after
    the ``--- SESSION START/END/COMPACTION ---`` header, e.g.:

        --- SESSION END at 07:14:20 ---
        Project: depthfusion
        Directory: /home/gregmorris/projects/depthfusion

    This corrects the back-compat rule that lets all no-YAML-frontmatter blocks
    through unfiltered: once project is parsed from session content, the project
    filter can properly exclude off-project session blocks.

    Returns None when no ``Project:`` line is found — callers treat None as
    unknown project and keep the back-compat include-all rule.
    """
    if not content:
        return None
    m = _SESSION_PROJECT_RE.search(content)
    return m.group(1) if m else None


def detect_mentioned_projects(
    query: str,
    available_projects: "set[str]",
) -> "frozenset[str]":
    """Return the subset of `available_projects` whose slugs appear in `query`.

    Used to widen the project filter when the user's query explicitly names a
    project that differs from the current working directory project. For example,
    "I'm working on the SkillForge router" from a depthfusion session should
    retrieve skillforge context even with cross_project=False.

    Matching is case-insensitive substring search on the query. Only slugs with
    ≥4 characters are matched to avoid false positives from short abbreviations.
    """
    if not query or not available_projects:
        return frozenset()
    query_lower = query.lower()
    return frozenset(
        p for p in available_projects
        if p and len(p) >= 4 and p.lower() in query_lower
    )
