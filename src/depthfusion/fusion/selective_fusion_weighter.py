"""Selective fusion weighter — TS-parity port of selective-fusion-weighter.ts.

Implements the sequential Mamba-style B/C/Δ gating with multiplicative
scoring to match the SkillForge TypeScript reference exactly.

Algorithmic differences from fusion/gates.py (SelectiveFusionGates / S-51):
  * B gate:   soft penalty (score × 0.1) below bGateMinSimilarity vs hard threshold
  * C gate:   sequential adjacent (lastEmbedding) vs max-pairwise over all candidates
  * Fused:    base × bGateValue × cGateValue × srcWeight (multiplicative) vs α-blend
  * No α parameter; no BM25 normalisation step

These differences preserve the TS `selectiveAttnresFusion` semantics:
multiplicative scoring means a weak B or C signal suppresses the block
even when base_score is high — matching Mamba's selective-state-space
behaviour at fusion time.

TS reference:
  packages/depthfusion-core/src/fusion/selective-fusion-weighter.ts

Backlog: T-447 (S-129)
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Defaults match TS SelectiveGateConfig.
_DEFAULT_B_GATE_MIN_SIMILARITY: float = 0.1
_DEFAULT_C_GATE_DECAY_RATIO: int = 3
_DEFAULT_C_GATE_ADJACENT_THRESHOLD: float = 0.3   # TS: `if cScore > 0.3`
_DEFAULT_DELTA_GATE_THRESHOLD: float = 0.05


@dataclass(frozen=True)
class SelectiveGateConfig:
    """Tunable parameters matching TS SelectiveGateConfig.

    b_gate_min_similarity:  cosine threshold; below it the block receives a
                            0.1× soft penalty rather than hard rejection.
    c_gate_decay_ratio:     denominator for the non-adjacent C gate value
                            (cGateValue = 1 / decayRatio when not adjacent).
    c_gate_adjacent_threshold: cosine threshold above which a block is
                                considered "adjacent" to the previous one.
    delta_gate_threshold:   minimum fused score for inclusion in results.
    """
    b_gate_min_similarity: float = _DEFAULT_B_GATE_MIN_SIMILARITY
    c_gate_decay_ratio: int = _DEFAULT_C_GATE_DECAY_RATIO
    c_gate_adjacent_threshold: float = _DEFAULT_C_GATE_ADJACENT_THRESHOLD
    delta_gate_threshold: float = _DEFAULT_DELTA_GATE_THRESHOLD

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "b_gate_min_similarity",
            max(0.0, min(1.0, self.b_gate_min_similarity)),
        )
        object.__setattr__(
            self, "c_gate_adjacent_threshold",
            max(0.0, min(1.0, self.c_gate_adjacent_threshold)),
        )
        object.__setattr__(
            self, "c_gate_decay_ratio",
            max(1, self.c_gate_decay_ratio),  # guard against div-by-zero
        )
        object.__setattr__(
            self, "delta_gate_threshold",
            max(0.0, self.delta_gate_threshold) if math.isfinite(self.delta_gate_threshold)
            else 0.0,
        )

    @classmethod
    def from_env(cls) -> "SelectiveGateConfig":
        """Read config from DEPTHFUSION_FUSION_GATES_* env vars.

        Env vars:
          DEPTHFUSION_FUSION_GATES_B_THRESHOLD       → b_gate_min_similarity
          DEPTHFUSION_FUSION_GATES_C_ADJACENT_THRESHOLD → c_gate_adjacent_threshold
          DEPTHFUSION_FUSION_GATES_C_DECAY_RATIO     → c_gate_decay_ratio
          DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD   → delta_gate_threshold
        """
        def _float(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.debug("SelectiveGateConfig: invalid %s=%r, using default", name, raw)
                return default

        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return max(1, int(raw))
            except ValueError:
                logger.debug("SelectiveGateConfig: invalid %s=%r, using default", name, raw)
                return default

        return cls(
            b_gate_min_similarity=_float(
                "DEPTHFUSION_FUSION_GATES_B_THRESHOLD", _DEFAULT_B_GATE_MIN_SIMILARITY,
            ),
            c_gate_adjacent_threshold=_float(
                "DEPTHFUSION_FUSION_GATES_C_ADJACENT_THRESHOLD",
                _DEFAULT_C_GATE_ADJACENT_THRESHOLD,
            ),
            c_gate_decay_ratio=_int(
                "DEPTHFUSION_FUSION_GATES_C_DECAY_RATIO", _DEFAULT_C_GATE_DECAY_RATIO,
            ),
            delta_gate_threshold=_float(
                "DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD", _DEFAULT_DELTA_GATE_THRESHOLD,
            ),
        )

    def version_id(self) -> str:
        """Deterministic 12-char hex snapshot ID (I-8 compliance, same pattern as GateConfig)."""
        fields = (
            f"b={max(0.0, min(1.0, self.b_gate_min_similarity)):.10f}",
            f"c_adj={max(0.0, min(1.0, self.c_gate_adjacent_threshold)):.10f}",
            f"c_decay={max(1, self.c_gate_decay_ratio)}",
            f"delta={max(0.0, self.delta_gate_threshold):.10f}",
        )
        raw = "|".join(fields).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:12]


@dataclass(frozen=True)
class WeightedGateDecision:
    """Per-block audit record for the sequential multiplicative algorithm."""
    chunk_id: str
    b_gate_value: float     # actual multiplier (b_score or b_score × 0.1)
    c_gate_value: float     # 1.0 (adjacent / first) or 1.0 / decay_ratio
    fused_score: float      # base × b_gate_value × c_gate_value × src_weight
    base_score: float
    src_weight: float
    passes_delta: bool

    @property
    def passes_all(self) -> bool:
        return self.passes_delta


@dataclass
class WeightedGateLog:
    """Per-query summary of a SelectiveFusionWeighter call.

    D-3 invariant: emitted for every query, even when no block is rejected.
    """
    total_candidates: int
    passed_delta: int
    b_gate_min_similarity: float
    c_gate_decay_ratio: int
    delta_gate_threshold: float
    config_version_id: str = ""
    decisions: list[WeightedGateDecision] = field(default_factory=list)


class SelectiveFusionWeighter:
    """Port of TS `selectiveAttnresFusion` — sequential multiplicative B/C/Δ gating.

    Processes blocks in order, carrying `lastEmbedding` forward for the C gate
    exactly as the TS implementation does.
    """

    def __init__(
        self,
        config: SelectiveGateConfig | None = None,
        source_weights: dict[str, float] | None = None,
    ) -> None:
        self._config = config or SelectiveGateConfig()
        self._source_weights: dict[str, float] = source_weights or {}

    @property
    def config(self) -> SelectiveGateConfig:
        return self._config

    def apply(
        self,
        blocks: list[dict],
        *,
        query_embedding: list[float] | None = None,
    ) -> tuple[list[dict], WeightedGateLog]:
        """Run sequential B/C/Δ gates over `blocks`, returning (survivors, log).

        Matches TS `selectiveAttnresFusion(chunks, queryEmbedding, sourceWeights, config)`.

        Blocks must have at minimum:
          - `chunk_id`: str
          - `score`: float (BM25 / RRF / source-weighted base score)
        Optional:
          - `embedding`: list[float] — used for B and C cosine computations
          - `source` / `file_path`: str — looked up in source_weights
        """
        config = self._config
        empty_log = WeightedGateLog(
            total_candidates=0, passed_delta=0,
            b_gate_min_similarity=config.b_gate_min_similarity,
            c_gate_decay_ratio=config.c_gate_decay_ratio,
            delta_gate_threshold=config.delta_gate_threshold,
            config_version_id=config.version_id(),
        )
        if not blocks:
            return [], empty_log

        decisions: list[WeightedGateDecision] = []
        survivors: list[tuple[float, dict]] = []
        last_embedding: list[float] | None = None
        passed_delta = 0

        for block in blocks:
            chunk_id = str(block.get("chunk_id", ""))
            base_score = float(block.get("score", 0.0))
            embedding: list[float] | None = block.get("embedding")
            source = str(block.get("source", block.get("file_path", "")))
            src_weight = float(self._source_weights.get(source, 1.0))

            # B gate — query similarity
            # TS: no embeddings → default 0.5 ("neutral pass-through")
            if query_embedding and embedding:
                b_score = _cosine(query_embedding, embedding)
            else:
                b_score = 0.5

            if b_score < config.b_gate_min_similarity:
                b_gate_value = b_score * 0.1   # soft penalty, not hard reject
            else:
                b_gate_value = b_score

            # C gate — sequential adjacent (lastEmbedding)
            # TS: first chunk → 1.0; adjacent (cScore > threshold) → 1.0;
            # not adjacent → 1.0 / decayRatio
            if last_embedding is None:
                c_gate_value = 1.0
            elif embedding:
                c_score = _cosine(last_embedding, embedding)
                c_gate_value = 1.0 if c_score > config.c_gate_adjacent_threshold \
                    else 1.0 / config.c_gate_decay_ratio
            else:
                # No embedding for C comparison → treat as not adjacent
                c_gate_value = 1.0 / config.c_gate_decay_ratio

            # Fused score — multiplicative (TS: chunk.score * bGate * cGate * srcWeight)
            fused = base_score * b_gate_value * c_gate_value * src_weight
            passes_delta = fused >= config.delta_gate_threshold

            if passes_delta:
                passed_delta += 1

            decisions.append(WeightedGateDecision(
                chunk_id=chunk_id,
                b_gate_value=round(b_gate_value, 4),
                c_gate_value=round(c_gate_value, 4),
                fused_score=round(fused, 4),
                base_score=round(base_score, 4),
                src_weight=round(src_weight, 4),
                passes_delta=passes_delta,
            ))

            if passes_delta:
                enriched = {
                    **block,
                    "gate_b_score": round(b_gate_value, 4),
                    "gate_c_score": round(c_gate_value, 4),
                    "gate_fused_score": round(fused, 4),
                }
                survivors.append((fused, enriched))

            # Advance sequential state (only when block has an embedding)
            if embedding:
                last_embedding = embedding

        survivors.sort(key=lambda t: -t[0])

        log = WeightedGateLog(
            total_candidates=len(blocks),
            passed_delta=passed_delta,
            b_gate_min_similarity=config.b_gate_min_similarity,
            c_gate_decay_ratio=config.c_gate_decay_ratio,
            delta_gate_threshold=config.delta_gate_threshold,
            config_version_id=config.version_id(),
            decisions=decisions,
        )
        return [b for _, b in survivors], log


# ---------------------------------------------------------------------------
# Similarity helper — intentionally duplicated from fusion/gates.py to keep
# this module import-free from other fusion internals.
# ---------------------------------------------------------------------------

def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity in [-1.0, 1.0]; 0.0 on degenerate inputs (never raises)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


__all__ = [
    "SelectiveGateConfig",
    "SelectiveFusionWeighter",
    "WeightedGateDecision",
    "WeightedGateLog",
]
