"""Query dispatcher — routes queries to the right retrieval strategy."""
from __future__ import annotations

from depthfusion.core.config import DepthFusionConfig

_RLM_TOKEN_THRESHOLD = 150_000

_VALID_STRATEGIES = frozenset({"weighted_fusion", "rrf", "rlm", "passthrough"})


class QueryDispatcher:
    """Routes a query to the right retrieval strategy based on context and config."""

    def __init__(self, config: DepthFusionConfig | None = None) -> None:
        self._config = config if config is not None else DepthFusionConfig()

    def dispatch(
        self,
        query: str,
        context_tokens: int,
        is_indexed: bool,
        force_strategy: str | None = None,
    ) -> str:
        """Return strategy name: 'weighted_fusion' | 'rrf' | 'rlm' | 'passthrough'.

        Rules (in priority order):
        1. force_strategy → use that (no validation against enabled flags)
        2. context_tokens > 150_000 and not is_indexed → rlm
           (fall back to rrf if rlm_enabled is False)
        3. is_indexed → weighted_fusion
           (fall back to rrf if fusion_enabled is False)
        4. else → rrf
        """
        cfg = self._config

        if force_strategy is not None:
            return force_strategy

        if context_tokens > _RLM_TOKEN_THRESHOLD and not is_indexed:
            if cfg.rlm_enabled:
                return "rlm"
            return "rrf"

        if is_indexed:
            if cfg.fusion_enabled:
                return "weighted_fusion"
            return "rrf"

        return "rrf"
