"""Selective fusion gates — Mamba B/C/Δ port (TS-1 / TG-11 / S-51).

Three-stage selective fusion filter applied after BM25 + optional
embedding retrieval, before reranking:

  * **B gate** — query-similarity gating. A block is kept only when its
    similarity to the query (cosine over embeddings, or normalised BM25
    score as a fallback) ≥ `b_threshold`. Models Mamba's *input*
    selectivity: "is this block about the query at all?"

  * **C gate** — topical-coherence gating. A block is kept only when at
    least one other block in the candidate pool is similar to it above
    `c_threshold`. Models Mamba's *content-dependent decay*: "is this
    block adjacent to other relevant blocks, or an orphan hit?"

  * **Δ gate** — fused-score threshold. Final filter on the α-blended
    score `α·attention_score + (1-α)·base_score`. Models Mamba's
    *output step size*: "does the combined signal clear the bar?"

Gate log (D-3 invariant)
========================
Every apply() call emits a complete `GateLog` with per-block decisions
AND per-query summary (counts at each stage, thresholds, α). The log
is the audit artefact regardless of whether any block was rejected —
the presence of the log proves the gate was consulted.

Graceful degradation
====================
- No embeddings available → B gate falls back to BM25 percentile rank
- No other candidates (n=1) → C gate passes trivially
- Empty input → returns empty output, emits a minimal log
- Any math failure → block kept (fail-open: retrieval correctness > gate signal)

Spec: docs/plans/v0.5/02-build-plan.md §TG-11,
      docs/depthfusion-skillforge-divergence.md §3c
Backlog: T-156 (S-51)
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Defaults aligned with docs/plans/v0.5/02-build-plan.md §TG-11:
#   DEPTHFUSION_FUSION_GATES_ALPHA = 0.3 (AttnRes α)
_DEFAULT_ALPHA = 0.30
_DEFAULT_B_THRESHOLD = 0.10   # similarity floor to enter the pool
_DEFAULT_C_THRESHOLD = 0.05   # adjacent-similarity floor
_DEFAULT_DELTA_THRESHOLD = 0.0  # final-score floor (0 = accept anything non-negative)


@dataclass(frozen=True)
class GateConfig:
    """Tunable parameters for the selective fusion gates.

    `alpha` controls the AttnRes blend: final_score = α·attn + (1-α)·base.
    Thresholds clamp to [0, 1] on construction to avoid operator error.
    """
    alpha: float = _DEFAULT_ALPHA
    b_threshold: float = _DEFAULT_B_THRESHOLD
    c_threshold: float = _DEFAULT_C_THRESHOLD
    delta_threshold: float = _DEFAULT_DELTA_THRESHOLD

    def __post_init__(self) -> None:
        # Frozen dataclass — use object.__setattr__ to clamp.
        object.__setattr__(self, "alpha", max(0.0, min(1.0, self.alpha)))
        object.__setattr__(self, "b_threshold", max(0.0, min(1.0, self.b_threshold)))
        object.__setattr__(self, "c_threshold", max(0.0, min(1.0, self.c_threshold)))
        # delta_threshold can legitimately exceed 1 when base_scores are
        # un-normalised BM25 values; allow any finite non-negative.
        object.__setattr__(
            self,
            "delta_threshold",
            max(0.0, self.delta_threshold) if math.isfinite(self.delta_threshold) else 0.0,
        )

    @classmethod
    def from_env(cls) -> "GateConfig":
        """Read gate config from DEPTHFUSION_FUSION_GATES_* env vars.

        Unset → defaults. Malformed → defaults (never raises at config time).
        """
        def _float(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.debug("GateConfig: invalid %s=%r, using default %s", name, raw, default)
                return default

        return cls(
            alpha=_float("DEPTHFUSION_FUSION_GATES_ALPHA", _DEFAULT_ALPHA),
            b_threshold=_float("DEPTHFUSION_FUSION_GATES_B_THRESHOLD", _DEFAULT_B_THRESHOLD),
            c_threshold=_float("DEPTHFUSION_FUSION_GATES_C_THRESHOLD", _DEFAULT_C_THRESHOLD),
            delta_threshold=_float(
                "DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD", _DEFAULT_DELTA_THRESHOLD,
            ),
        )

    def version_id(self) -> str:
        """Deterministic 12-char hex ID of this config snapshot (I-8 compliance).

        Stable under:
          - Field order (tuple ordering is fixed).
          - Value equality after post-init clamping (two configs with pre-
            clamp alpha=-1 and alpha=0 produce the same id since both
            clamp to 0.0).
          - Process / host / interpreter — hashlib.sha256 is deterministic
            across all of them; format string pins the float precision.

        Changes when:
          - Any field changes value (even small: 0.30 vs 0.30001 → different).
          - Defaults change in a future release (the tuple shape is the hash
            input, so adding a field breaks the ID across versions —
            intentional; a v0.6 `GateConfig` should have a v0.6-distinct id).

        Used by `record_gate_log(config_version_id=...)` to let auditors
        reproduce a gate decision against the exact config that produced
        it (per DR-018 §4 ratification of I-8). See
        docs/plans/v0.5/03-skillforge-integration.md §3.3.5 for the
        contract.

        Edge-case handling:
        - `-0.0` vs `0.0`: Python's `max(0.0, min(1.0, -0.0))` in __post_init__
          already normalises to `+0.0` (first-arg-wins on IEEE 754 tie), so
          callers passing `alpha=-0.0` get the same ID as `alpha=0.0`. We
          still call `_normalise_float` below as defense-in-depth against
          future interpreter changes and as documentation of intent.
        - `NaN`: `math.isfinite` guards in __post_init__ replace NaN with
          0.0 only for delta_threshold; NaN in alpha/b/c will format as
          "nan" — deterministic across calls but two separately-produced
          NaN configs will hash-equal even though NaN != NaN. Accepted:
          NaN configs are operator error, and reproducing the operator
          error deterministically is still audit-useful.
        """
        def _normalise_float(v: float) -> float:
            # Collapse -0.0 → 0.0 so that signed zero can never produce two
            # distinct IDs for configs that compare equal (0.0 == -0.0 is True).
            return 0.0 if v == 0.0 else v

        # Format each float with a fixed precision so "0.3" and "0.30000000"
        # hash identically — avoid repr() since its output is not stable
        # across Python minor versions for some float values.
        fields = (
            f"alpha={_normalise_float(self.alpha):.10f}",
            f"b_threshold={_normalise_float(self.b_threshold):.10f}",
            f"c_threshold={_normalise_float(self.c_threshold):.10f}",
            f"delta_threshold={_normalise_float(self.delta_threshold):.10f}",
        )
        raw = "|".join(fields).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:12]


@dataclass(frozen=True)
class GateDecision:
    """Per-block audit record. All three gate verdicts plus the scores.

    One of these is emitted for every candidate block, pass or fail —
    the gate log is an observability artefact, not a filter output.
    """
    chunk_id: str
    passes_b: bool
    passes_c: bool
    passes_delta: bool
    b_score: float        # query similarity
    c_score: float        # max adjacent similarity (0.0 if n≤1)
    base_score: float     # pre-gate score (e.g. BM25 or RRF-fused)
    fused_score: float    # α·b_score + (1-α)·base_score

    @property
    def passes_all(self) -> bool:
        return self.passes_b and self.passes_c and self.passes_delta


@dataclass(frozen=True)
class GateLog:
    """Per-query summary of a selective-fusion-gates invocation.

    D-3 invariant: a log is emitted for EVERY query, even when no block
    is rejected. `config_version_id` carries the I-8 ratification tag.
    """
    alpha: float
    b_threshold: float
    c_threshold: float
    delta_threshold: float
    total_candidates: int
    passed_b: int
    passed_c: int
    passed_delta: int
    decisions: list[GateDecision] = field(default_factory=list)
    # Set by the caller (typically `RecallPipeline.apply_fusion_gates`) from
    # `GateConfig.version_id()`. I-8 compliance — auditors reproduce gate
    # decisions by looking up this snapshot ID. Default empty string is the
    # "snapshot pointer not wired" sentinel for callers that invoke the gates
    # directly without going through the retrieval pipeline.
    config_version_id: str = ""


# ---------------------------------------------------------------------------
# Similarity helpers — duplicated from retrieval.hybrid to keep fusion/ free
# of retrieval-layer imports (decoupling for the v0.5.0 TS parity).
# ---------------------------------------------------------------------------

def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity in [-1.0, 1.0]; 0.0 on degenerate inputs (never raises)."""
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


def _bm25_percentile(score: float, all_scores: list[float]) -> float:
    """Rank `score` within `all_scores`, return percentile in [0.0, 1.0].

    Fallback "similarity" when embeddings aren't available — the top
    BM25 hit gets ~1.0, the bottom gets ~0.0. Ties share the same
    percentile so the gate doesn't arbitrarily reject one of two
    identically-scored chunks.
    """
    if not all_scores:
        return 0.0
    n = len(all_scores)
    if n == 1:
        return 1.0 if score > 0 else 0.0
    # Count scores strictly less than this one → percentile rank
    below = sum(1 for s in all_scores if s < score)
    return below / (n - 1)


# ---------------------------------------------------------------------------
# SelectiveFusionGates — the public entry point
# ---------------------------------------------------------------------------

class SelectiveFusionGates:
    """Apply B/C/Δ gates to a candidate block pool and emit an audit log."""

    def __init__(self, config: GateConfig | None = None) -> None:
        self._config = config or GateConfig()

    @property
    def config(self) -> GateConfig:
        return self._config

    def apply(
        self,
        blocks: list[dict],
        *,
        query_embedding: list[float] | None = None,
    ) -> tuple[list[dict], GateLog]:
        """Run the three gates over `blocks`.

        Each block is expected to have at minimum:
          - `chunk_id`: str
          - `score`: float (BM25 / RRF / source-weighted)
        Optional keys used when present:
          - `embedding`: list[float] — chunk vector for B and C gates
          - `snippet` / `content`: passed through unchanged

        Returns:
          (surviving_blocks, GateLog). Surviving blocks are sorted by
          `fused_score` descending and have `gate_b_score`, `gate_c_score`,
          `gate_fused_score` attached.
        """
        config = self._config

        if not blocks:
            empty_log = GateLog(
                alpha=config.alpha,
                b_threshold=config.b_threshold,
                c_threshold=config.c_threshold,
                delta_threshold=config.delta_threshold,
                total_candidates=0, passed_b=0, passed_c=0, passed_delta=0,
                decisions=[],
            )
            return [], empty_log

        # B gate — per-block query similarity
        # Scores are coerced through `float()` so numpy scalars (from embedding
        # backends) become native Python floats — otherwise they'd round-trip
        # through json.dumps(default=str) as strings and silently corrupt
        # downstream log parsers.
        base_scores = [float(b.get("score", 0.0)) for b in blocks]
        embeddings: list[list[float] | None] = [b.get("embedding") for b in blocks]
        b_scores = self._compute_b_scores(
            embeddings=embeddings,
            base_scores=base_scores,
            query_embedding=query_embedding,
        )

        # Normalise base_scores to [0,1] percentiles BEFORE the α blend so
        # both operands share the same scale. Without this, raw BM25 scores
        # (unbounded) dominate the blend and the B signal becomes invisible —
        # a default α=0.3 blend of `0.5 * 0.3 + 20 * 0.7 = 14.15` effectively
        # ignores the B contribution. Normalising per-query keeps α's semantic
        # meaning ("how much weight does query-similarity get vs BM25 rank?").
        base_percentiles = [_bm25_percentile(s, base_scores) for s in base_scores]

        # C gate — per-block max adjacent similarity (pairwise over candidates)
        c_scores = self._compute_c_scores(embeddings=embeddings, b_scores=b_scores)

        # Δ gate — threshold on α-blended fused score.
        # Sequential gate counting: passed_c only counts blocks that ALSO
        # passed B; passed_delta only blocks that passed all three.
        # This matches the TS reference and makes the log stages readable
        # as a funnel ("B: 20 → C: 12 → Δ: 8") rather than independent counts.
        decisions: list[GateDecision] = []
        passed_b = passed_c = passed_delta = 0
        for i, block in enumerate(blocks):
            fused = config.alpha * b_scores[i] + (1.0 - config.alpha) * base_percentiles[i]
            p_b = b_scores[i] >= config.b_threshold
            # C gate has an exemption: when there's only 1 candidate, coherence
            # is trivially undefined — pass it through rather than reject.
            p_c = len(blocks) <= 1 or c_scores[i] >= config.c_threshold
            p_delta = fused >= config.delta_threshold

            if p_b:
                passed_b += 1
                if p_c:
                    passed_c += 1
                    if p_delta:
                        passed_delta += 1

            decisions.append(GateDecision(
                chunk_id=str(block.get("chunk_id", f"idx{i}")),
                passes_b=p_b,
                passes_c=p_c,
                passes_delta=p_delta,
                b_score=round(b_scores[i], 4),
                c_score=round(c_scores[i], 4),
                base_score=round(base_scores[i], 4),
                fused_score=round(fused, 4),
            ))

        log = GateLog(
            alpha=config.alpha,
            b_threshold=config.b_threshold,
            c_threshold=config.c_threshold,
            delta_threshold=config.delta_threshold,
            total_candidates=len(blocks),
            passed_b=passed_b,
            passed_c=passed_c,
            passed_delta=passed_delta,
            decisions=decisions,
        )

        # Surviving blocks, sorted by fused_score desc
        survivors: list[tuple[float, dict]] = []
        for block, dec in zip(blocks, decisions, strict=True):
            if not dec.passes_all:
                continue
            enriched = {
                **block,
                "gate_b_score": dec.b_score,
                "gate_c_score": dec.c_score,
                "gate_fused_score": dec.fused_score,
            }
            survivors.append((dec.fused_score, enriched))
        survivors.sort(key=lambda t: -t[0])
        return [b for _, b in survivors], log

    # ------------------------------------------------------------------
    # Internal scoring helpers
    # ------------------------------------------------------------------

    def _compute_b_scores(
        self,
        *,
        embeddings: list[list[float] | None],
        base_scores: list[float],
        query_embedding: list[float] | None,
    ) -> list[float]:
        """B score = cosine(query, block) when that block has an embedding,
        else BM25 percentile (per-block fallback).

        Pre-review behaviour was "if ANY block has an embedding, use cosine
        for ALL blocks" — which penalised embedding-less blocks with
        b_score=0.0 even when their BM25 score was excellent. The
        per-block fallback ensures no block is rejected by the B gate
        just because it happens to lack a stored embedding.
        """
        if not query_embedding:
            # No query embedding at all — use BM25 percentile for every block.
            return [_bm25_percentile(s, base_scores) for s in base_scores]

        scores: list[float] = []
        for emb, base in zip(embeddings, base_scores, strict=True):
            if emb:
                scores.append(_cosine(query_embedding, emb))
            else:
                # Per-block fallback: use BM25 percentile for this block alone.
                scores.append(_bm25_percentile(base, base_scores))
        return scores

    def _compute_c_scores(
        self,
        *,
        embeddings: list[list[float] | None],
        b_scores: list[float],
    ) -> list[float]:
        """C score = max adjacent similarity.

        With embeddings: max pairwise cosine between block i and any other block.
        Without embeddings: max adjacent B-score differential (proxy — blocks
        whose B scores are close to another's are topically "adjacent").
        """
        n = len(b_scores)
        if n <= 1:
            return [0.0] * n

        has_embs = any(e for e in embeddings)
        c_scores: list[float] = []

        if has_embs:
            for i, emb_i in enumerate(embeddings):
                if not emb_i:
                    c_scores.append(0.0)
                    continue
                best = 0.0
                for j, emb_j in enumerate(embeddings):
                    if i == j or not emb_j:
                        continue
                    sim = _cosine(emb_i, emb_j)
                    if sim > best:
                        best = sim
                c_scores.append(best)
        else:
            # Fallback: proximity in B-score space → 1 - |B_i - B_j| scaled
            for i, bi in enumerate(b_scores):
                best = 0.0
                for j, bj in enumerate(b_scores):
                    if i == j:
                        continue
                    prox = max(0.0, 1.0 - abs(bi - bj))
                    if prox > best:
                        best = prox
                c_scores.append(best)

        return c_scores


__all__ = [
    "GateConfig",
    "GateDecision",
    "GateLog",
    "SelectiveFusionGates",
]
