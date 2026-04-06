"""Haiku-based semantic reranker for BM25 results (VPS Tier 1)."""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

try:
    import anthropic
    _ANTHROPIC_IMPORTABLE = True
except ImportError:
    anthropic = None  # type: ignore[assignment]
    _ANTHROPIC_IMPORTABLE = False

_RERANK_PROMPT = """\
You are a relevance ranker. Given a search query and a list of memory blocks, \
return a JSON array of indices (0-based) sorted from most to least relevant to the query.

Query: {query}

Blocks:
{blocks_text}

Return ONLY a JSON array of indices, e.g. [2, 0, 1]. No explanation."""


class HaikuReranker:
    """Reranks BM25 results using claude-haiku for semantic relevance.

    Degrades gracefully to passthrough when ANTHROPIC_API_KEY is absent
    or the anthropic SDK is not installed.
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self._client = None
        if _ANTHROPIC_IMPORTABLE and (os.environ.get("DEPTHFUSION_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
            self._client = anthropic.Anthropic(api_key=os.environ.get("DEPTHFUSION_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))

    def is_available(self) -> bool:
        """Return True if the reranker can make API calls."""
        return self._client is not None

    def rerank(
        self,
        query: str,
        blocks: list[dict],
        top_k: int = 3,
    ) -> list[dict]:
        """Rerank blocks by relevance to query. Returns top_k blocks.

        Falls back to original order (truncated to top_k) if unavailable,
        API call fails, or response is not valid JSON.
        """
        if not blocks:
            return blocks
        if not self.is_available():
            return blocks[:top_k]

        blocks_text = "\n".join(
            f"[{i}] {b.get('snippet', b.get('chunk_id', ''))[:300]}"
            for i, b in enumerate(blocks)
        )
        prompt = _RERANK_PROMPT.format(query=query, blocks_text=blocks_text)

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            indices = json.loads(raw)
            if not isinstance(indices, list):
                raise ValueError("Response is not a list")
            # Filter valid indices, deduplicate, preserve order
            seen: set[int] = set()
            ordered: list[dict] = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(blocks) and idx not in seen:
                    ordered.append(blocks[idx])
                    seen.add(idx)
            # If reranker returned fewer than top_k, append remaining in original order
            for i, b in enumerate(blocks):
                if i not in seen and len(ordered) < top_k:
                    ordered.append(b)
            return ordered[:top_k]
        except Exception as exc:
            logger.debug("Reranker fallback (error: %s)", exc)
            return blocks[:top_k]
