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
"""
from __future__ import annotations

import logging
import math
import os
import re
from enum import Enum
from typing import Any

from depthfusion.retrieval.reranker import HaikuReranker

logger = logging.getLogger(__name__)

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

        Reads DEPTHFUSION_MODE (local|vps) and queries TierManager when in vps mode.
        Falls back to VPS_TIER1 if TierManager is unavailable (storage not yet installed).
        """
        install_mode = os.environ.get("DEPTHFUSION_MODE", "local")
        if install_mode != "vps":
            return cls(mode=PipelineMode.LOCAL)
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
      * Block project differs           → EXCLUDED
    """
    if cross_project or current_project is None:
        return blocks

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
        if project is None or project == current_project:
            filtered.append(block)
    return filtered
