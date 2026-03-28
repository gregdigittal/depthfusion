# DepthFusion v0.3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Augment DepthFusion with haiku semantic reranking (Tier 1), ChromaDB vector retrieval (Tier 2), auto-capture hooks, and a CLI installer — making it the foremost Claude Code memory tool.

**Architecture:** BM25 stays as the keyword precision layer in all modes. VPS Tier 1 adds a haiku reranker on top of BM25 top-10. VPS Tier 2 adds ChromaDB initial retrieval fused with BM25 before the reranker. Pre/PostCompact hooks auto-capture session decisions without manual /learn.

**Tech Stack:** Python 3.10+, anthropic SDK (existing), chromadb (Tier 2 optional), argparse, pytest

---

## File Map

**Created:**
- `src/depthfusion/retrieval/__init__.py`
- `src/depthfusion/retrieval/bm25.py` — BM25 class + tokenizer extracted from server.py
- `src/depthfusion/retrieval/reranker.py` — haiku relevance reranker
- `src/depthfusion/retrieval/hybrid.py` — RRF fusion + pipeline (BM25→reranker or BM25+ChromaDB→reranker)
- `src/depthfusion/capture/__init__.py`
- `src/depthfusion/capture/auto_learn.py` — heuristic extractor (local) + haiku summarizer (VPS)
- `src/depthfusion/capture/compressor.py` — .tmp → structured discovery (haiku)
- `src/depthfusion/storage/__init__.py`
- `src/depthfusion/storage/vector_store.py` — ChromaDB persistent wrapper
- `src/depthfusion/storage/tier_manager.py` — corpus detection, tier routing, promotion
- `src/depthfusion/install/__init__.py`
- `src/depthfusion/install/install.py` — CLI installer
- `src/depthfusion/install/migrate.py` — Tier 1 → Tier 2 indexer
- `~/.claude/hooks/depthfusion-pre-compact.sh`
- `~/.claude/hooks/depthfusion-post-compact.sh`
- `tests/test_retrieval/test_bm25.py`
- `tests/test_retrieval/test_reranker.py`
- `tests/test_retrieval/test_hybrid.py`
- `tests/test_capture/test_auto_learn.py`
- `tests/test_capture/test_compressor.py`
- `tests/test_storage/test_vector_store.py`
- `tests/test_storage/test_tier_manager.py`
- `tests/test_install/test_install.py`

**Modified:**
- `src/depthfusion/mcp/server.py` — add 3 new tools, import from retrieval/, tier routing
- `pyproject.toml` — add vps-tier1 and vps-tier2 optional dep groups

---

## Task 1: Extract BM25 into `retrieval/bm25.py`

**Files:**
- Create: `src/depthfusion/retrieval/__init__.py`
- Create: `src/depthfusion/retrieval/bm25.py`
- Create: `tests/test_retrieval/__init__.py`
- Create: `tests/test_retrieval/test_bm25.py`
- Modify: `src/depthfusion/mcp/server.py` (import from retrieval.bm25, remove inline defs)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_retrieval/__init__.py
# (empty)
```

```python
# tests/test_retrieval/test_bm25.py
import pytest
from depthfusion.retrieval.bm25 import BM25, tokenize


def test_tokenize_basic():
    tokens = tokenize("Hello World from Python")
    assert "hello" in tokens
    assert "world" in tokens
    assert "python" in tokens


def test_tokenize_removes_stopwords():
    tokens = tokenize("the quick brown fox")
    assert "the" not in tokens
    assert "quick" in tokens


def test_bm25_scores_relevant_doc_higher():
    corpus = [
        "VPS server IP address configuration",
        "cooking recipe pasta ingredients",
        "server deployment configuration guide",
    ]
    bm25 = BM25([tokenize(d) for d in corpus])
    scores = bm25.rank_all(tokenize("VPS server configuration"))
    top_idx = scores[0][0]
    assert top_idx in (0, 2)  # VPS or deployment doc should rank first


def test_bm25_zero_score_for_no_overlap():
    corpus = ["completely unrelated text about cooking"]
    bm25 = BM25([tokenize(d) for d in corpus])
    scores = bm25.rank_all(tokenize("VPS server deployment"))
    assert scores[0][1] == 0.0


def test_bm25_length_normalization():
    # Long doc with few relevant terms should score lower than short doc
    long_doc = "a " * 500 + "VPS server"
    short_doc = "VPS server configuration"
    bm25 = BM25([tokenize(long_doc), tokenize(short_doc)])
    scores = bm25.rank_all(tokenize("VPS server"))
    assert scores[0][0] == 1  # short doc should rank higher


def test_bm25_rank_all_sorted_descending():
    corpus = ["python code review", "javascript code", "python testing"]
    bm25 = BM25([tokenize(d) for d in corpus])
    ranked = bm25.rank_all(tokenize("python"))
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/gregmorris/Development/Projects/depthfusion
source .venv/bin/activate
python -m pytest tests/test_retrieval/test_bm25.py -v 2>&1 | head -20
```

Expected: `ERROR collecting tests/test_retrieval/test_bm25.py` — module not found

- [ ] **Step 3: Create `src/depthfusion/retrieval/__init__.py`**

```python
# src/depthfusion/retrieval/__init__.py
```

- [ ] **Step 4: Create `src/depthfusion/retrieval/bm25.py`**

```python
"""BM25 retrieval — extracted from mcp/server.py for standalone use."""
from __future__ import annotations

import math
import re

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "was", "are", "were", "be", "been", "have",
    "has", "do", "does", "did", "will", "would", "could", "should", "this",
    "that", "it", "not", "so", "if", "as", "up", "out", "just", "also",
})


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens, removing stopwords."""
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{1,}\b", text.lower())
    return [w for w in words if w not in _STOPWORDS]


class BM25:
    """BM25 scorer for small corpora. No external dependencies.

    k1=1.5 (term saturation), b=0.75 (length normalization).
    IDF uses the smoothed Robertson formula to prevent negative scores.
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.N = len(corpus_tokens)
        self.avgdl = sum(len(d) for d in corpus_tokens) / max(self.N, 1)
        self._tf: list[dict[str, int]] = []
        self._df: dict[str, int] = {}
        for doc in corpus_tokens:
            tf: dict[str, int] = {}
            for term in doc:
                tf[term] = tf.get(term, 0) + 1
            self._tf.append(tf)
            for term in set(doc):
                self._df[term] = self._df.get(term, 0) + 1
        self._dl = [len(d) for d in corpus_tokens]

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def score(self, query_terms: list[str], doc_idx: int) -> float:
        tf = self._tf[doc_idx]
        dl = self._dl[doc_idx]
        result = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = self._idf(term)
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            result += idf * (numerator / denominator)
        return result

    def rank_all(self, query_terms: list[str]) -> list[tuple[int, float]]:
        """Return (doc_idx, bm25_score) sorted descending."""
        scores = [(i, self.score(query_terms, i)) for i in range(self.N)]
        return sorted(scores, key=lambda x: -x[1])
```

- [ ] **Step 5: Update `server.py` to import from retrieval.bm25**

Replace the inline `_STOPWORDS`, `_tokenize_bm25`, and `_BM25` in server.py with:

```python
# At top of server.py, add import:
from depthfusion.retrieval.bm25 import BM25 as _BM25, tokenize as _tokenize_bm25
```

Then delete lines 130–191 (the inline `_STOPWORDS`, `_tokenize_bm25`, `_BM25` definitions).

- [ ] **Step 6: Run all tests**

```bash
python -m pytest tests/test_retrieval/test_bm25.py tests/test_analyzer/test_mcp_server.py -v 2>&1 | tail -10
```

Expected: All PASS, no regressions in mcp_server tests.

- [ ] **Step 7: Full suite green check**

```bash
python -m pytest -q 2>&1 | tail -5
```

Expected: `286 passed` (or more if counting new tests).

- [ ] **Step 8: Commit**

```bash
git add src/depthfusion/retrieval/ tests/test_retrieval/ src/depthfusion/mcp/server.py
git commit -m "refactor(retrieval): extract BM25 from server.py into standalone retrieval/bm25.py"
```

---

## Task 2: Haiku Reranker — `retrieval/reranker.py`

**Files:**
- Create: `src/depthfusion/retrieval/reranker.py`
- Create: `tests/test_retrieval/test_reranker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_retrieval/test_reranker.py
import json
import pytest
from unittest.mock import MagicMock, patch
from depthfusion.retrieval.reranker import HaikuReranker


SAMPLE_BLOCKS = [
    {"chunk_id": "vps-instance", "source": "memory", "score": 5.0,
     "snippet": "VPS server at 77.42.45.197, SSH access via key auth"},
    {"chunk_id": "preferences", "source": "memory", "score": 3.0,
     "snippet": "Coding preferences: TypeScript strict mode, no any types"},
    {"chunk_id": "project-patterns", "source": "memory", "score": 1.0,
     "snippet": "Cross-project patterns for architecture decisions"},
]


def test_reranker_is_disabled_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = HaikuReranker()
    assert not r.is_available()


def test_reranker_passthrough_when_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = HaikuReranker()
    result = r.rerank("VPS server IP", SAMPLE_BLOCKS, top_k=3)
    assert result == SAMPLE_BLOCKS  # unchanged passthrough


def test_reranker_returns_top_k(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="[0, 2, 1]")]
    with patch("depthfusion.retrieval.reranker.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic.Anthropic.return_value = mock_client
        r = HaikuReranker()
        result = r.rerank("VPS server", SAMPLE_BLOCKS, top_k=2)
    assert len(result) == 2
    assert result[0]["chunk_id"] == "vps-instance"
    assert result[1]["chunk_id"] == "project-patterns"


def test_reranker_fallback_on_bad_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="I cannot determine relevance")]
    with patch("depthfusion.retrieval.reranker.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic.Anthropic.return_value = mock_client
        r = HaikuReranker()
        result = r.rerank("VPS server", SAMPLE_BLOCKS, top_k=3)
    # Should fallback to original BM25 order
    assert result == SAMPLE_BLOCKS


def test_reranker_handles_empty_blocks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    r = HaikuReranker()
    result = r.rerank("anything", [], top_k=3)
    assert result == []
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_retrieval/test_reranker.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'depthfusion.retrieval.reranker'`

- [ ] **Step 3: Create `src/depthfusion/retrieval/reranker.py`**

```python
"""Haiku semantic reranker — uses Claude haiku to rerank BM25 top-N results.

Requires ANTHROPIC_API_KEY. Gracefully degrades to passthrough when unavailable.
Adds semantic understanding on top of BM25 without embedding infrastructure.
Cost: ~$0.00025 per reranking call (10 passages, haiku-tier).
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

try:
    import anthropic
    _ANTHROPIC_IMPORTABLE = True
except ImportError:
    _ANTHROPIC_IMPORTABLE = False

_RERANK_PROMPT = """\
Query: {query}

Passages to rank (by index):
{passages}

Return ONLY a JSON array of the passage indices in descending order of relevance \
to the query. Include only passages that are genuinely relevant. Example: [2, 0, 4]"""


class HaikuReranker:
    """Rerank BM25 results using Claude haiku for semantic understanding.

    When unavailable (no API key, import error, or network failure), returns
    the input unchanged so the BM25 ranking is preserved.
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self._client = None
        if _ANTHROPIC_IMPORTABLE and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                self._client = anthropic.Anthropic()
            except Exception:
                pass

    def is_available(self) -> bool:
        return self._client is not None

    def rerank(
        self,
        query: str,
        blocks: list[dict],
        top_k: int = 3,
    ) -> list[dict]:
        """Rerank blocks by relevance to query. Returns top_k results.

        Falls back to original BM25 order on any failure.
        """
        if not blocks:
            return []
        if not self.is_available():
            return blocks[:top_k] if len(blocks) > top_k else blocks

        candidates = blocks[:10]  # never send more than 10 to haiku
        passages = "\n\n".join(
            f"[{i}] {b['snippet'][:400]}" for i, b in enumerate(candidates)
        )
        prompt = _RERANK_PROMPT.format(query=query, passages=passages)

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
            match = re.search(r"\[[\d,\s]*\]", text)
            if not match:
                logger.debug("Reranker: no JSON array in response, using BM25 order")
                return blocks[:top_k]
            indices = json.loads(match.group())
            reranked = [candidates[i] for i in indices if 0 <= i < len(candidates)]
            if not reranked:
                return blocks[:top_k]
            return reranked[:top_k]
        except Exception as exc:
            logger.debug(f"Reranker error (using BM25 fallback): {exc}")
            return blocks[:top_k]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_retrieval/test_reranker.py -v 2>&1 | tail -10
```

Expected: 5 PASS

- [ ] **Step 5: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: `291 passed` (286 + 5 new)

- [ ] **Step 6: Commit**

```bash
git add src/depthfusion/retrieval/reranker.py tests/test_retrieval/test_reranker.py
git commit -m "feat(retrieval): add HaikuReranker for semantic reranking of BM25 results"
```

---

## Task 3: Recall Pipeline — `retrieval/hybrid.py`

**Files:**
- Create: `src/depthfusion/retrieval/hybrid.py`
- Create: `tests/test_retrieval/test_hybrid.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_retrieval/test_hybrid.py
import pytest
from unittest.mock import MagicMock, patch
from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode


def _make_blocks(n: int) -> list[dict]:
    return [
        {"chunk_id": f"doc{i}", "source": "memory", "score": float(n - i),
         "snippet": f"content about topic {i}"}
        for i in range(n)
    ]


def test_pipeline_mode_local_returns_bm25_only():
    p = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = _make_blocks(5)
    result = p.apply_reranker(blocks, "query", top_k=3)
    # local mode: no reranker, just top_k slice
    assert len(result) == 3
    assert result[0]["chunk_id"] == "doc0"


def test_pipeline_rrf_fusion_merges_two_ranked_lists():
    p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
    bm25 = [{"chunk_id": "a", "score": 10.0}, {"chunk_id": "b", "score": 5.0}]
    vector = [{"chunk_id": "b", "score": 0.9}, {"chunk_id": "c", "score": 0.8}]
    fused = p.rrf_fuse(bm25, vector, k=60)
    # "b" appears in both lists, should rank higher than "a" or "c" alone
    chunk_ids = [b["chunk_id"] for b in fused]
    assert "b" in chunk_ids
    assert chunk_ids.index("b") <= 1  # b in top 2


def test_pipeline_rrf_handles_empty_vector_list():
    p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
    bm25 = [{"chunk_id": "a", "score": 10.0}]
    fused = p.rrf_fuse(bm25, [], k=60)
    assert fused == bm25


def test_pipeline_mode_from_env(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    # With small corpus, should default to tier1
    with patch("depthfusion.retrieval.hybrid.TierManager") as mock_tm:
        from depthfusion.storage.tier_manager import Tier, TierConfig
        mock_tm.return_value.detect_tier.return_value = TierConfig(
            tier=Tier.VPS_TIER1, corpus_size=10, threshold=500,
            sessions_until_promotion=490, mode="vps"
        )
        p = RecallPipeline.from_env()
    assert p.mode == PipelineMode.VPS_TIER1
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_retrieval/test_hybrid.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/depthfusion/retrieval/hybrid.py`**

```python
"""Recall pipeline — orchestrates BM25 + optional haiku reranker + optional ChromaDB.

PipelineMode.LOCAL:       BM25 only, no API calls
PipelineMode.VPS_TIER1:   BM25 top-10 → HaikuReranker → top-k
PipelineMode.VPS_TIER2:   ChromaDB top-20 + BM25 top-10 → RRF fusion → HaikuReranker → top-k
"""
from __future__ import annotations

import os
from enum import Enum

from depthfusion.retrieval.reranker import HaikuReranker


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
        """Build pipeline from environment variables."""
        install_mode = os.environ.get("DEPTHFUSION_MODE", "local")
        if install_mode != "vps":
            return cls(mode=PipelineMode.LOCAL)
        try:
            from depthfusion.storage.tier_manager import TierManager, Tier
            tm = TierManager()
            cfg = tm.detect_tier()
            if cfg.tier == Tier.VPS_TIER2:
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
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_retrieval/test_hybrid.py -v 2>&1 | tail -10
```

Expected: 4 PASS

- [ ] **Step 5: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: `295 passed`

- [ ] **Step 6: Commit**

```bash
git add src/depthfusion/retrieval/hybrid.py tests/test_retrieval/test_hybrid.py
git commit -m "feat(retrieval): add RecallPipeline with RRF fusion and haiku reranker integration"
```

---

## Task 4: Auto-Learn Capture — `capture/auto_learn.py`

**Files:**
- Create: `src/depthfusion/capture/__init__.py`
- Create: `src/depthfusion/capture/auto_learn.py`
- Create: `tests/test_capture/__init__.py`
- Create: `tests/test_capture/test_auto_learn.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_capture/__init__.py
# (empty)
```

```python
# tests/test_capture/test_auto_learn.py
import pytest
import tempfile
from pathlib import Path
from depthfusion.capture.auto_learn import HeuristicExtractor, extract_key_decisions


SAMPLE_SESSION = """\
# Goal: implement user auth
## Progress
- Task 1: DONE — added JWT middleware
→ Decision: use RS256 not HS256 for JWT signing
NOTE: refresh tokens stored in httpOnly cookies only
IMPORTANT: never log the JWT payload
WARNING: session.tmp files are cleared on compact

## Key Findings
**ANTHROPIC_API_KEY** must be set in systemd EnvironmentFile

## Architecture
- Chose PostgreSQL over SQLite for concurrent writes
"""

CORRUPT_SESSION = "}\x00\x01invalid\xff"
EMPTY_SESSION = "   \n\n  "


def test_extract_decisions_from_valid_content():
    decisions = extract_key_decisions(SAMPLE_SESSION)
    assert len(decisions) > 0
    # Should capture → decision arrow lines
    assert any("RS256" in d for d in decisions)
    # Should capture NOTE: lines
    assert any("httpOnly" in d for d in decisions)


def test_extract_decisions_from_empty_content():
    decisions = extract_key_decisions(EMPTY_SESSION)
    assert decisions == []


def test_extract_decisions_from_corrupt_content():
    # Should not raise, should return empty or partial
    decisions = extract_key_decisions(CORRUPT_SESSION)
    assert isinstance(decisions, list)


def test_heuristic_extractor_from_file(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-test.tmp"
    session_file.write_text(SAMPLE_SESSION, encoding="utf-8")
    extractor = HeuristicExtractor()
    output = extractor.extract_from_file(session_file)
    assert output is not None
    assert "RS256" in output or "JWT" in output


def test_heuristic_extractor_skips_empty_file(tmp_path):
    empty_file = tmp_path / "empty.tmp"
    empty_file.write_text(EMPTY_SESSION, encoding="utf-8")
    extractor = HeuristicExtractor()
    output = extractor.extract_from_file(empty_file)
    assert output is None


def test_heuristic_extractor_file_not_found():
    extractor = HeuristicExtractor()
    output = extractor.extract_from_file(Path("/nonexistent/file.tmp"))
    assert output is None
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_capture/test_auto_learn.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/depthfusion/capture/__init__.py`** (empty)

- [ ] **Step 4: Create `src/depthfusion/capture/auto_learn.py`**

```python
"""Auto-learn extraction — heuristic (local) and haiku-based (VPS).

HeuristicExtractor: regex-based extraction of key decisions from .tmp files.
No API calls — safe for local mode.

HaikuSummarizer: calls Claude haiku to produce a structured discovery summary.
Requires ANTHROPIC_API_KEY. Used in VPS mode by PostCompact hook.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DECISION_PATTERNS = [
    (r"^→\s+(.{10,300})", re.MULTILINE),
    (r"^DECISION:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^NOTE:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^IMPORTANT:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^WARNING:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^\*\*(.{10,150})\*\*", re.MULTILINE),
]

_MIN_CONTENT_LENGTH = 20


def extract_key_decisions(content: str) -> list[str]:
    """Extract decision-like lines from session content using heuristic patterns."""
    if not content or len(content.strip()) < _MIN_CONTENT_LENGTH:
        return []
    decisions: list[str] = []
    for pattern, flags in _DECISION_PATTERNS:
        try:
            matches = re.findall(pattern, content, flags)
            decisions.extend(m.strip() for m in matches if len(m.strip()) >= 10)
        except Exception:
            continue
    # Deduplicate preserving order, cap at 50
    seen: set[str] = set()
    unique: list[str] = []
    for d in decisions:
        if d not in seen:
            seen.add(d)
            unique.append(d)
        if len(unique) >= 50:
            break
    return unique


class HeuristicExtractor:
    """Extracts key decisions from a .tmp session file without API calls."""

    def extract_from_file(self, path: Path) -> str | None:
        """Read file and return a markdown summary string, or None if nothing found."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        decisions = extract_key_decisions(content)
        if not decisions:
            return None
        lines = [f"# Auto-Learned: {path.stem}", ""]
        lines.extend(f"- {d}" for d in decisions)
        return "\n".join(lines)


class HaikuSummarizer:
    """Summarize a .tmp session file into a structured discovery using Claude haiku.

    Requires anthropic SDK and ANTHROPIC_API_KEY. Gracefully degrades to
    HeuristicExtractor when unavailable.
    """

    _PROMPT = """\
Extract the key architectural decisions, facts, and implementation choices from this \
Claude Code session transcript. Focus on: decisions made, errors encountered and fixed, \
specific values (IPs, keys, versions), and patterns established. Ignore conversational \
filler. Format as a concise markdown document with ## sections.

Session content (truncated to 3000 chars):
{content}"""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self._client = None
        try:
            import anthropic
            import os
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except ImportError:
            pass

    def is_available(self) -> bool:
        return self._client is not None

    def summarize_file(self, path: Path) -> str | None:
        """Summarize a session file. Falls back to heuristic if haiku unavailable."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not content.strip():
            return None

        if not self.is_available():
            return HeuristicExtractor().extract_from_file(path)

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": self._PROMPT.format(content=content[:3000]),
                }],
            )
            summary = msg.content[0].text.strip()
            if not summary:
                return None
            return f"# Session Summary: {path.stem}\n\n{summary}"
        except Exception as exc:
            logger.warning(f"Haiku summarizer failed ({exc}), falling back to heuristic")
            return HeuristicExtractor().extract_from_file(path)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_capture/test_auto_learn.py -v 2>&1 | tail -10
```

Expected: 6 PASS

- [ ] **Step 6: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: `301 passed`

- [ ] **Step 7: Commit**

```bash
git add src/depthfusion/capture/ tests/test_capture/
git commit -m "feat(capture): add HeuristicExtractor and HaikuSummarizer for auto-learn"
```

---

## Task 5: Session Compressor — `capture/compressor.py`

**Files:**
- Create: `src/depthfusion/capture/compressor.py`
- Create: `tests/test_capture/test_compressor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_capture/test_compressor.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from depthfusion.capture.compressor import SessionCompressor


def test_compressor_writes_discovery_file(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-test.tmp"
    session_file.write_text("# Goal: test\n→ Decision: use BM25\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()

    with patch.object(SessionCompressor, "is_available", return_value=False):
        c = SessionCompressor()
        output_path = c.compress(session_file, output_dir=discoveries_dir)

    assert output_path is not None
    assert output_path.exists()
    content = output_path.read_text()
    assert "BM25" in content or "Auto-Learned" in content


def test_compressor_skips_empty_file(tmp_path):
    session_file = tmp_path / "empty.tmp"
    session_file.write_text("  \n  ", encoding="utf-8")
    c = SessionCompressor()
    result = c.compress(session_file, output_dir=tmp_path)
    assert result is None


def test_compressor_output_filename_format(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-myfeature.tmp"
    session_file.write_text("# Goal\n→ Decision: important thing\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    with patch.object(SessionCompressor, "is_available", return_value=False):
        c = SessionCompressor()
        output_path = c.compress(session_file, output_dir=discoveries_dir)
    # Output should be in discoveries dir
    assert output_path.parent == discoveries_dir
    assert output_path.suffix == ".md"


def test_compressor_does_not_overwrite_existing(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-test.tmp"
    session_file.write_text("→ Decision: first\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    existing = discoveries_dir / "2026-03-28-goal-test-autocapture.md"
    existing.write_text("# existing content", encoding="utf-8")

    with patch.object(SessionCompressor, "is_available", return_value=False):
        c = SessionCompressor()
        result = c.compress(session_file, output_dir=discoveries_dir)
    # Should not overwrite
    assert existing.read_text() == "# existing content"
    assert result is None  # skipped because already exists
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_capture/test_compressor.py -v 2>&1 | head -15
```

- [ ] **Step 3: Create `src/depthfusion/capture/compressor.py`**

```python
"""SessionCompressor — converts .tmp session files into structured discovery files.

Uses HaikuSummarizer when available, falls back to HeuristicExtractor.
Idempotent: skips files already compressed (output file exists).
"""
from __future__ import annotations

import logging
from pathlib import Path

from depthfusion.capture.auto_learn import HaikuSummarizer, HeuristicExtractor

logger = logging.getLogger(__name__)

_DEFAULT_DISCOVERIES = Path.home() / ".claude" / "shared" / "discoveries"


class SessionCompressor:
    """Compress a .tmp session file into a discovery markdown file."""

    def __init__(self):
        self._summarizer = HaikuSummarizer()

    def is_available(self) -> bool:
        return self._summarizer.is_available()

    def compress(
        self,
        session_file: Path,
        output_dir: Path | None = None,
    ) -> Path | None:
        """Compress session_file to output_dir.

        Returns the output Path on success, None if skipped (empty or already exists).
        """
        out_dir = output_dir or _DEFAULT_DISCOVERIES
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = session_file.stem
        output_path = out_dir / f"{stem}-autocapture.md"

        if output_path.exists():
            logger.debug(f"Skipping {session_file.name} — already compressed")
            return None

        summary = self._summarizer.summarize_file(session_file)
        if not summary:
            # Try heuristic as last resort
            summary = HeuristicExtractor().extract_from_file(session_file)
        if not summary:
            return None

        output_path.write_text(summary, encoding="utf-8")
        logger.info(f"Compressed {session_file.name} → {output_path.name}")
        return output_path
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_capture/test_compressor.py -v 2>&1 | tail -10
```

Expected: 4 PASS

- [ ] **Step 5: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: `305 passed`

- [ ] **Step 6: Commit**

```bash
git add src/depthfusion/capture/compressor.py tests/test_capture/test_compressor.py
git commit -m "feat(capture): add SessionCompressor for .tmp → discovery file conversion"
```

---

## Task 6: Storage Layer — `storage/vector_store.py` + `storage/tier_manager.py`

**Files:**
- Create: `src/depthfusion/storage/__init__.py`
- Create: `src/depthfusion/storage/vector_store.py`
- Create: `src/depthfusion/storage/tier_manager.py`
- Create: `tests/test_storage/__init__.py`
- Create: `tests/test_storage/test_vector_store.py`
- Create: `tests/test_storage/test_tier_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_storage/__init__.py
# (empty)
```

```python
# tests/test_storage/test_vector_store.py
import pytest
from depthfusion.storage.vector_store import ChromaDBStore, is_chromadb_available


def test_is_chromadb_available_returns_bool():
    result = is_chromadb_available()
    assert isinstance(result, bool)


@pytest.mark.skipif(not is_chromadb_available(), reason="chromadb not installed")
def test_chromadb_store_add_and_query(tmp_path):
    store = ChromaDBStore(persist_dir=tmp_path / "vectors")
    store.add_document("doc1", "VPS server SSH configuration", {"source": "memory"})
    store.add_document("doc2", "cooking pasta recipe", {"source": "memory"})
    results = store.query("VPS server", top_k=1)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "doc1"


@pytest.mark.skipif(not is_chromadb_available(), reason="chromadb not installed")
def test_chromadb_store_upsert_idempotent(tmp_path):
    store = ChromaDBStore(persist_dir=tmp_path / "vectors")
    store.add_document("doc1", "original content", {"source": "memory"})
    store.add_document("doc1", "updated content", {"source": "memory"})
    assert store.count() == 1  # upsert, not duplicate


def test_chromadb_store_unavailable_raises_import_error(monkeypatch):
    import sys
    # Simulate chromadb not installed
    monkeypatch.setitem(sys.modules, "chromadb", None)
    from importlib import reload
    import depthfusion.storage.vector_store as vs
    reload(vs)
    if not vs.is_chromadb_available():
        with pytest.raises(ImportError, match="chromadb"):
            vs.ChromaDBStore()
    # Reload back to normal
    monkeypatch.delitem(sys.modules, "chromadb")
    reload(vs)
```

```python
# tests/test_storage/test_tier_manager.py
import pytest
from unittest.mock import patch
from depthfusion.storage.tier_manager import TierManager, Tier


def test_detect_tier_local_mode(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    tm = TierManager()
    cfg = tm.detect_tier()
    assert cfg.tier == Tier.LOCAL
    assert cfg.mode == "local"


def test_detect_tier_vps_tier1_small_corpus(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    tm = TierManager()
    with patch.object(tm, "_count_corpus", return_value=10):
        cfg = tm.detect_tier()
    assert cfg.tier == Tier.VPS_TIER1
    assert cfg.sessions_until_promotion == 490


def test_detect_tier_vps_tier2_large_corpus(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    tm = TierManager()
    with patch.object(tm, "_count_corpus", return_value=501):
        cfg = tm.detect_tier()
    assert cfg.tier == Tier.VPS_TIER2
    assert cfg.sessions_until_promotion == 0


def test_boundary_exactly_at_threshold(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    tm = TierManager()
    with patch.object(tm, "_count_corpus", return_value=500):
        cfg = tm.detect_tier()
    assert cfg.tier == Tier.VPS_TIER2  # >= threshold = tier2


def test_autopromote_default_false_local(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.delenv("DEPTHFUSION_TIER_AUTOPROMOTE", raising=False)
    tm = TierManager()
    assert not tm.auto_promote


def test_autopromote_true_vps(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_AUTOPROMOTE", "true")
    tm = TierManager()
    assert tm.auto_promote
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_storage/ -v 2>&1 | head -15
```

- [ ] **Step 3: Create `src/depthfusion/storage/__init__.py`** (empty)

- [ ] **Step 4: Create `src/depthfusion/storage/vector_store.py`**

```python
"""ChromaDB vector store wrapper for DepthFusion Tier 2."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PERSIST_DIR = Path.home() / ".claude" / ".depthfusion_vectors"

try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except (ImportError, Exception):
    _CHROMADB_AVAILABLE = False


def is_chromadb_available() -> bool:
    return _CHROMADB_AVAILABLE


class ChromaDBStore:
    """Persistent ChromaDB vector store. Tier 2 only."""

    def __init__(self, persist_dir: Optional[Path] = None):
        if not _CHROMADB_AVAILABLE:
            raise ImportError(
                "chromadb not installed. Run: pip install 'depthfusion[vps-tier2]'"
            )
        dir_ = persist_dir or _PERSIST_DIR
        dir_.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(dir_))
        self._collection = self._client.get_or_create_collection(
            name="memory_corpus",
            metadata={"hnsw:space": "cosine"},
        )

    def add_document(self, doc_id: str, content: str, metadata: dict) -> None:
        """Add or update a document (upsert)."""
        self._collection.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata],
        )

    def query(self, query_text: str, top_k: int = 20) -> list[dict]:
        """Return top_k most similar documents."""
        n = min(top_k, self.count())
        if n == 0:
            return []
        results = self._collection.query(query_texts=[query_text], n_results=n)
        output = []
        for i, doc_id in enumerate(results["ids"][0]):
            dist = results["distances"][0][i] if results.get("distances") else 0.0
            output.append({
                "chunk_id": doc_id,
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "score": max(0.0, 1.0 - dist),  # cosine distance → similarity
            })
        return output

    def count(self) -> int:
        return self._collection.count()
```

- [ ] **Step 5: Create `src/depthfusion/storage/tier_manager.py`**

```python
"""Tier detection and routing for DepthFusion install modes."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Tier(Enum):
    LOCAL = "local"
    VPS_TIER1 = "vps-tier1"
    VPS_TIER2 = "vps-tier2"


@dataclass
class TierConfig:
    tier: Tier
    corpus_size: int
    threshold: int
    sessions_until_promotion: int
    mode: str


class TierManager:
    """Detect active tier based on corpus size and environment config."""

    def __init__(self):
        self.threshold = int(os.environ.get("DEPTHFUSION_TIER_THRESHOLD", "500"))
        self.mode = os.environ.get("DEPTHFUSION_MODE", "local")
        _default_autopromote = "true" if self.mode == "vps" else "false"
        self.auto_promote = (
            os.environ.get("DEPTHFUSION_TIER_AUTOPROMOTE", _default_autopromote).lower()
            == "true"
        )

    def _count_corpus(self) -> int:
        home = Path.home()
        count = 0
        for path, pattern in [
            (home / ".claude" / "sessions", "*.tmp"),
            (home / ".claude" / "shared" / "discoveries", "*.md"),
        ]:
            if path.exists():
                count += len(list(path.glob(pattern)))
        memory = home / ".claude" / "projects" / "-home-gregmorris" / "memory"
        if memory.exists():
            count += len([f for f in memory.glob("*.md") if f.name != "MEMORY.md"])
        return count

    def detect_tier(self) -> TierConfig:
        corpus = self._count_corpus()
        if self.mode == "local":
            tier = Tier.LOCAL
        elif corpus >= self.threshold:
            tier = Tier.VPS_TIER2
        else:
            tier = Tier.VPS_TIER1
        return TierConfig(
            tier=tier,
            corpus_size=corpus,
            threshold=self.threshold,
            sessions_until_promotion=max(0, self.threshold - corpus),
            mode=self.mode,
        )
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_storage/ -v 2>&1 | tail -15
```

Expected: All PASS (chromadb tests skip gracefully if not installed)

- [ ] **Step 7: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: `315+ passed`

- [ ] **Step 8: Commit**

```bash
git add src/depthfusion/storage/ tests/test_storage/
git commit -m "feat(storage): add ChromaDBStore and TierManager for Tier 2 vector retrieval"
```

---

## Task 7: Pre/PostCompact Hooks

**Files:**
- Create: `~/.claude/hooks/depthfusion-pre-compact.sh`
- Create: `~/.claude/hooks/depthfusion-post-compact.sh`
- Modify: `~/.claude/settings.json` (or `~/.claude/settings.local.json`) — add hook entries

- [ ] **Step 1: Create `~/.claude/hooks/depthfusion-pre-compact.sh`**

```bash
#!/bin/bash
# depthfusion-pre-compact.sh — snapshot active state before context compaction
# Runs as PreCompact hook. Writes snapshot for post-compact to consume.
# No API calls. Always fast.

set -euo pipefail

SNAPSHOT_FILE="$HOME/.claude/.depthfusion-compact-snapshot.json"
SESSIONS_DIR="$HOME/.claude/sessions"

# Find most recently modified .tmp file
LATEST_TMP=$(ls -t "$SESSIONS_DIR"/*.tmp 2>/dev/null | head -1 || echo "")

# Capture git context if in a git repo
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Write snapshot JSON
cat > "$SNAPSHOT_FILE" <<EOF
{
  "timestamp": "$TIMESTAMP",
  "project": "$GIT_PROJECT",
  "branch": "$GIT_BRANCH",
  "latest_session_file": "$LATEST_TMP",
  "mode": "${DEPTHFUSION_MODE:-local}"
}
EOF

echo "[depthfusion-pre-compact] Snapshot written: $SNAPSHOT_FILE" >&2
```

- [ ] **Step 2: Create `~/.claude/hooks/depthfusion-post-compact.sh`**

```bash
#!/bin/bash
# depthfusion-post-compact.sh — auto-capture session decisions after compaction
# Reads snapshot written by pre-compact. Compresses latest session file.
# VPS mode: uses haiku summarization. Local mode: heuristic extraction only.

set -euo pipefail

SNAPSHOT_FILE="$HOME/.claude/.depthfusion-compact-snapshot.json"
DISCOVERIES_DIR="$HOME/.claude/shared/discoveries"
DEPTHFUSION_DIR="$HOME/Development/Projects/depthfusion"

if [ ! -f "$SNAPSHOT_FILE" ]; then
  echo "[depthfusion-post-compact] No snapshot found, skipping" >&2
  exit 0
fi

LATEST_SESSION=$(python3 -c "
import json, sys
with open('$SNAPSHOT_FILE') as f:
    d = json.load(f)
print(d.get('latest_session_file', ''))
" 2>/dev/null || echo "")

if [ -z "$LATEST_SESSION" ] || [ ! -f "$LATEST_SESSION" ]; then
  echo "[depthfusion-post-compact] No session file found, skipping" >&2
  exit 0
fi

# Run compression via depthfusion Python module
if [ -d "$DEPTHFUSION_DIR/.venv" ]; then
  PYTHON="$DEPTHFUSION_DIR/.venv/bin/python"
else
  PYTHON="python3"
fi

"$PYTHON" -c "
import sys
sys.path.insert(0, '$DEPTHFUSION_DIR/src')
from pathlib import Path
from depthfusion.capture.compressor import SessionCompressor
c = SessionCompressor()
result = c.compress(Path('$LATEST_SESSION'), output_dir=Path('$DISCOVERIES_DIR'))
if result:
    print(f'[depthfusion-post-compact] Compressed to: {result}')
else:
    print('[depthfusion-post-compact] Nothing to compress (empty or already done)')
" 2>&1

# Clean up snapshot
rm -f "$SNAPSHOT_FILE"
```

- [ ] **Step 3: Make hooks executable**

```bash
chmod +x ~/.claude/hooks/depthfusion-pre-compact.sh
chmod +x ~/.claude/hooks/depthfusion-post-compact.sh
```

- [ ] **Step 4: Register hooks in Claude Code settings**

Read `~/.claude/settings.json`. In the `hooks` section, add:

```json
"PreCompact": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "bash ~/.claude/hooks/depthfusion-pre-compact.sh"
      }
    ]
  }
],
"PostCompact": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "bash ~/.claude/hooks/depthfusion-post-compact.sh"
      }
    ]
  }
]
```

- [ ] **Step 5: Verify hooks are syntactically valid**

```bash
bash -n ~/.claude/hooks/depthfusion-pre-compact.sh && echo "pre-compact: OK"
bash -n ~/.claude/hooks/depthfusion-post-compact.sh && echo "post-compact: OK"
```

Expected: both print `OK`

- [ ] **Step 6: Manual smoke test of pre-compact**

```bash
DEPTHFUSION_MODE=vps bash ~/.claude/hooks/depthfusion-pre-compact.sh
cat ~/.claude/.depthfusion-compact-snapshot.json
```

Expected: JSON with timestamp, project, branch fields

- [ ] **Step 7: Commit**

```bash
git add ~/.claude/hooks/depthfusion-pre-compact.sh ~/.claude/hooks/depthfusion-post-compact.sh
git commit -m "feat(hooks): add PreCompact/PostCompact auto-capture hooks"
```

---

## Task 8: Install CLI — `install/install.py` + `install/migrate.py`

**Files:**
- Create: `src/depthfusion/install/__init__.py`
- Create: `src/depthfusion/install/install.py`
- Create: `src/depthfusion/install/migrate.py`
- Create: `tests/test_install/__init__.py`
- Create: `tests/test_install/test_install.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_install/__init__.py
# (empty)
```

```python
# tests/test_install/test_install.py
import pytest
import subprocess
import sys


def test_install_help():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--mode" in result.stdout


def test_install_local_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "local", "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout.lower() or "local" in result.stdout.lower()


def test_install_vps_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "vps", "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "vps" in result.stdout.lower() or "dry-run" in result.stdout.lower()


def test_install_rejects_invalid_mode():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.install",
         "--mode", "cloud"],
        capture_output=True, text=True
    )
    assert result.returncode != 0


def test_migrate_help():
    result = subprocess.run(
        [sys.executable, "-m", "depthfusion.install.migrate", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_install/test_install.py -v 2>&1 | head -15
```

- [ ] **Step 3: Create `src/depthfusion/install/__init__.py`** (empty)

- [ ] **Step 4: Create `src/depthfusion/install/install.py`**

```python
"""DepthFusion installer — configures hooks and environment for local or VPS mode."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path.home() / ".claude" / "hooks"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

_LOCAL_ENV_LINES = [
    "DEPTHFUSION_MODE=local",
    "DEPTHFUSION_TIER_AUTOPROMOTE=false",
]
_VPS_ENV_LINES = [
    "DEPTHFUSION_MODE=vps",
    "DEPTHFUSION_TIER_AUTOPROMOTE=true",
    "DEPTHFUSION_TIER_THRESHOLD=500",
    "DEPTHFUSION_RERANKER_ENABLED=true",
]


def _print_step(msg: str, dry_run: bool = False) -> None:
    prefix = "[DRY-RUN]" if dry_run else "[INSTALL]"
    print(f"{prefix} {msg}")


def install_local(dry_run: bool = False) -> None:
    _print_step("Configuring DepthFusion for LOCAL mode", dry_run)
    _print_step("  - BM25 retrieval: enabled", dry_run)
    _print_step("  - Haiku reranker: DISABLED (no API calls in local mode)", dry_run)
    _print_step("  - PostCompact hook: heuristic extraction only", dry_run)
    _print_step("  - ChromaDB: not required", dry_run)
    if not dry_run:
        _write_env_config(_LOCAL_ENV_LINES)
        _register_hooks()
    _print_step("Local install complete.", dry_run)
    _print_step("Add to your environment: DEPTHFUSION_MODE=local", dry_run)


def install_vps(dry_run: bool = False, tier_threshold: int = 500) -> None:
    _print_step(f"Configuring DepthFusion for VPS mode (tier threshold: {tier_threshold})", dry_run)
    _print_step("  - BM25 retrieval: enabled", dry_run)
    _print_step("  - Haiku reranker (Tier 1): enabled (requires ANTHROPIC_API_KEY)", dry_run)
    _print_step(f"  - ChromaDB vector store (Tier 2): enabled at {tier_threshold}+ sessions", dry_run)
    _print_step("  - PreCompact + PostCompact auto-capture hooks: enabled", dry_run)
    if not dry_run:
        env_lines = _VPS_ENV_LINES.copy()
        env_lines.append(f"DEPTHFUSION_TIER_THRESHOLD={tier_threshold}")
        _write_env_config(env_lines)
        _register_hooks()
        _check_anthropic_key()
    _print_step("VPS install complete.", dry_run)
    _print_step("Ensure ANTHROPIC_API_KEY is set for haiku reranker.", dry_run)


def _write_env_config(lines: list[str]) -> None:
    """Write environment config to ~/.claude/depthfusion.env"""
    env_file = Path.home() / ".claude" / "depthfusion.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote config: {env_file}")


def _register_hooks() -> None:
    """Add hooks to Claude Code settings.json if not already present."""
    if not SETTINGS_PATH.exists():
        print(f"  Warning: {SETTINGS_PATH} not found, skipping hook registration")
        return
    with open(SETTINGS_PATH) as f:
        settings = json.load(f)
    hooks = settings.setdefault("hooks", {})
    hook_dir = HOOKS_DIR
    for event, script in [
        ("PreCompact", "depthfusion-pre-compact.sh"),
        ("PostCompact", "depthfusion-post-compact.sh"),
    ]:
        script_path = hook_dir / script
        if not script_path.exists():
            print(f"  Warning: {script_path} not found — skipping {event} hook")
            continue
        existing = hooks.get(event, [])
        cmd = f"bash {script_path}"
        already_registered = any(
            h.get("command") == cmd or
            any(ih.get("command") == cmd for ih in h.get("hooks", []))
            for h in existing
        )
        if not already_registered:
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": cmd}]}
            )
            print(f"  Registered {event} hook: {script}")
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def _check_anthropic_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  Warning: ANTHROPIC_API_KEY not set. Haiku reranker will be disabled.")
        print("  Set it with: export ANTHROPIC_API_KEY=sk-...")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DepthFusion installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", choices=["local", "vps"], required=True,
                        help="Install mode: 'local' (no API calls) or 'vps' (haiku + ChromaDB)")
    parser.add_argument("--tier-threshold", type=int, default=500,
                        help="Session count threshold for Tier 2 promotion (default: 500)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without making changes")
    args = parser.parse_args()

    if args.mode == "local":
        install_local(dry_run=args.dry_run)
    else:
        install_vps(dry_run=args.dry_run, tier_threshold=args.tier_threshold)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Create `src/depthfusion/install/migrate.py`**

```python
"""Tier 1 → Tier 2 migration — indexes existing session/memory files into ChromaDB."""
from __future__ import annotations

import argparse
from pathlib import Path


def run_migration(dry_run: bool = False) -> None:
    """Index all existing content into ChromaDB for Tier 2."""
    home = Path.home()
    sources = [
        (home / ".claude" / "sessions", "*.tmp", "session"),
        (home / ".claude" / "shared" / "discoveries", "*.md", "discovery"),
        (home / ".claude" / "projects" / "-home-gregmorris" / "memory", "*.md", "memory"),
    ]

    files_to_index: list[tuple[Path, str]] = []
    for directory, pattern, source_type in sources:
        if directory.exists():
            for f in directory.glob(pattern):
                if f.name not in ("MEMORY.md", "README.md"):
                    files_to_index.append((f, source_type))

    print(f"Found {len(files_to_index)} files to index into ChromaDB Tier 2")

    if dry_run:
        for f, src in files_to_index[:10]:
            print(f"  [DRY-RUN] Would index: {f.name} ({src})")
        if len(files_to_index) > 10:
            print(f"  ... and {len(files_to_index) - 10} more")
        return

    from depthfusion.storage.vector_store import ChromaDBStore, is_chromadb_available
    if not is_chromadb_available():
        print("Error: chromadb not installed. Run: pip install 'depthfusion[vps-tier2]'")
        raise SystemExit(1)

    store = ChromaDBStore()
    indexed = 0
    for file_path, source_type in files_to_index:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                store.add_document(
                    doc_id=file_path.stem,
                    content=content[:8000],  # ChromaDB token limit guard
                    metadata={"source": source_type, "filename": file_path.name},
                )
                indexed += 1
                if indexed % 10 == 0:
                    print(f"  Indexed {indexed}/{len(files_to_index)}...")
        except Exception as exc:
            print(f"  Warning: could not index {file_path.name}: {exc}")

    print(f"Migration complete. Indexed {indexed} documents into ChromaDB.")
    print(f"Total vectors in store: {store.count()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate DepthFusion to Tier 2 (ChromaDB)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List files without indexing")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_install/test_install.py -v 2>&1 | tail -10
```

Expected: 5 PASS

- [ ] **Step 7: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: `320+ passed`

- [ ] **Step 8: Commit**

```bash
git add src/depthfusion/install/ tests/test_install/
git commit -m "feat(install): add CLI installer for local/VPS modes and Tier 1→2 migration script"
```

---

## Task 9: Update `server.py` — 3 New MCP Tools + Tier Routing

**Files:**
- Modify: `src/depthfusion/mcp/server.py`

This task wires all the new modules into the MCP interface.

- [ ] **Step 1: Add 3 new tools to `TOOLS` dict and `_TOOL_FLAGS`**

In `server.py`, after the existing TOOLS dict, add:

```python
TOOLS: dict[str, str] = {
    "depthfusion_status": "Return current DepthFusion component status",
    "depthfusion_recall_relevant": "Retrieve most relevant session blocks for a query",
    "depthfusion_tag_session": "Tag a session file with metadata",
    "depthfusion_publish_context": "Publish a context item to the bus",
    "depthfusion_run_recursive": "Run recursive LLM on large content",
    # v0.3.0 additions
    "depthfusion_tier_status": "Return corpus size, active tier, and promotion estimate",
    "depthfusion_auto_learn": "Trigger auto-learning extraction from recent session files",
    "depthfusion_compress_session": "Compress a specific .tmp session file into a discovery file",
}

_TOOL_FLAGS: dict[str, str | None] = {
    "depthfusion_status": None,
    "depthfusion_recall_relevant": None,
    "depthfusion_tag_session": None,
    "depthfusion_publish_context": "router_enabled",
    "depthfusion_run_recursive": "rlm_enabled",
    "depthfusion_tier_status": None,
    "depthfusion_auto_learn": None,
    "depthfusion_compress_session": None,
}
```

- [ ] **Step 2: Add dispatch cases in `_dispatch_tool`**

```python
elif tool_name == "depthfusion_tier_status":
    return _tool_tier_status()
elif tool_name == "depthfusion_auto_learn":
    return _tool_auto_learn(arguments)
elif tool_name == "depthfusion_compress_session":
    return _tool_compress_session(arguments)
```

- [ ] **Step 3: Implement the 3 new tool functions**

```python
def _tool_tier_status() -> str:
    try:
        from depthfusion.storage.tier_manager import TierManager
        tm = TierManager()
        cfg = tm.detect_tier()
        return json.dumps({
            "tier": cfg.tier.value,
            "corpus_size": cfg.corpus_size,
            "threshold": cfg.threshold,
            "sessions_until_promotion": cfg.sessions_until_promotion,
            "mode": cfg.mode,
            "auto_promote": tm.auto_promote,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_auto_learn(arguments: dict) -> str:
    """Trigger auto-learn extraction from recent .tmp session files."""
    from pathlib import Path
    max_files = int(arguments.get("max_files", 5))
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.exists():
        return json.dumps({"compressed": 0, "message": "No sessions directory"})
    try:
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        recent = sorted(sessions_dir.glob("*.tmp"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        results = []
        for tmp in recent:
            out = compressor.compress(tmp)
            if out:
                results.append(str(out.name))
        return json.dumps({
            "compressed": len(results),
            "files": results,
            "message": f"Auto-learned from {len(results)} session files",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "compressed": 0})


def _tool_compress_session(arguments: dict) -> str:
    """Compress a specific .tmp file into a discovery file."""
    from pathlib import Path
    session_path_str = arguments.get("session_path", "")
    if not session_path_str:
        return json.dumps({"error": "session_path argument required"})
    try:
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        out = compressor.compress(Path(session_path_str))
        if out:
            return json.dumps({"success": True, "output": str(out)})
        return json.dumps({"success": False, "message": "Nothing to compress (empty or already done)"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
```

- [ ] **Step 4: Update server version string**

In `_process_request`, change:
```python
"serverInfo": {"name": "depthfusion", "version": "0.3.0"},
```

Also update `pyproject.toml` version to `"0.3.0"`.

- [ ] **Step 5: Add tier-aware recall path**

In `_tool_recall`, after building `raw_blocks` and before the BM25 section, add:

```python
    # VPS Tier 1+2: apply pipeline (reranker / ChromaDB fusion)
    from depthfusion.retrieval.hybrid import RecallPipeline
    pipeline = RecallPipeline.from_env()
```

Then at the end, replace the final `blocks_out` slice with:

```python
    # Apply reranker (no-op in local mode, haiku in vps-tier1, haiku after RRF in vps-tier2)
    # Convert scored_blocks to the reranker's expected format first
    reranker_input = [
        {**raw_blocks[idx], "snippet": raw_blocks[idx]["content"][:snippet_len].strip(), "score": score}
        for idx, score in weighted if score > 0.0
    ]
    # Deduplicate by file_stem before reranking
    seen_files: set[str] = set()
    deduped = []
    for b in reranker_input:
        if b["file_stem"] not in seen_files:
            seen_files.add(b["file_stem"])
            deduped.append(b)
    blocks_out = pipeline.apply_reranker(deduped, query, top_k=top_k)
    # Truncate snippets
    for b in blocks_out:
        if "snippet" not in b:
            b["snippet"] = b.get("content", "")[:snippet_len].strip()
        elif len(b["snippet"]) > snippet_len:
            b["snippet"] = b["snippet"][:snippet_len] + "…"
```

- [ ] **Step 6: Run existing MCP server tests**

```bash
python -m pytest tests/test_analyzer/test_mcp_server.py -v 2>&1 | tail -15
```

Expected: All PASS

- [ ] **Step 7: Full suite**

```bash
python -m pytest -q 2>&1 | tail -3
```

Expected: All previous tests pass + new tool tests

- [ ] **Step 8: Commit**

```bash
git add src/depthfusion/mcp/server.py pyproject.toml
git commit -m "feat(mcp): add depthfusion_tier_status, depthfusion_auto_learn, depthfusion_compress_session tools; tier-aware recall pipeline"
```

---

## Task 10: `pyproject.toml` Optional Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add optional dependency groups**

```toml
[project.optional-dependencies]
rlm = ["rlms"]
vps-tier1 = []  # anthropic SDK is already available in the environment; no additional install
vps-tier2 = ["chromadb>=0.4"]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "mypy>=1.0",
    "ruff>=0.4",
]
```

- [ ] **Step 2: Verify install works**

```bash
pip install -e ".[vps-tier2]" --dry-run 2>&1 | head -10
```

Expected: Shows chromadb would be installed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): add vps-tier1 and vps-tier2 optional dependency groups"
```

---

## Task 11: README Update + CIQS Benchmark

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run CIQS benchmark**

Run the same 5-query test that established the v0.2.0 baseline:

```python
# Run in a Python shell with venv activated:
import subprocess, json

test_queries = [
    "VPS server IP address SSH access",
    "coding preferences commit style TypeScript",
    "what was I recently working on",
    "SkillForge depthfusion integration status",
    "review gate patterns architectural decisions",
]

for query in test_queries:
    result = subprocess.run(
        ["python", "-m", "depthfusion.mcp.server"],
        input=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call",
            "params":{"name":"depthfusion_recall_relevant",
                      "arguments":{"query":query,"top_k":3}}}),
        capture_output=True, text=True
    )
    # Parse and print top result for each query
    resp = json.loads(result.stdout)
    blocks = json.loads(resp["result"]["content"][0]["text"]).get("blocks", [])
    top = blocks[0]["chunk_id"] if blocks else "NONE"
    print(f"Query: {query[:40]!r:42} → {top}")
```

Record results. Compare against v0.2.0 baseline.

- [ ] **Step 2: Update README.md**

Update README with:
1. CIQS performance table (v0.1.0, v0.2.0, v0.3.0-local, v0.3.0-vps-tier1)
2. Install instructions for both modes
3. Architecture diagram (text-based)
4. Honest statement of limitations per mode

```markdown
## Install

### Local mode (zero external dependencies)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m depthfusion.install.install --mode local
claude mcp add depthfusion --scope user -- $(pwd)/.venv/bin/python -m depthfusion.mcp.server
```

### VPS mode (haiku reranker + ChromaDB Tier 2)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[vps-tier2]"
export ANTHROPIC_API_KEY=sk-...
python -m depthfusion.install.install --mode vps
claude mcp add depthfusion --scope user -- $(pwd)/.venv/bin/python -m depthfusion.mcp.server
```
```

- [ ] **Step 3: Run full test suite final check**

```bash
python -m pytest -v 2>&1 | tail -20
python -m pytest --cov=depthfusion --cov-report=term-missing 2>&1 | tail -5
```

Expected: All tests PASS, coverage ≥ 80% on new modules

- [ ] **Step 4: Run install dry-run verification**

```bash
python -m depthfusion.install.install --mode local --dry-run
python -m depthfusion.install.install --mode vps --dry-run
python -m depthfusion.install.migrate --dry-run
```

Expected: All print success, exit code 0

- [ ] **Step 5: Final commit**

```bash
git add README.md
git commit -m "docs: update README with v0.3.0 install instructions, CIQS table, architecture overview"
```

---

## Self-Review Checklist

- [x] Spec coverage: all 10 spec requirements have tasks
- [x] No placeholders: all steps contain actual code
- [x] Type consistency: `TierConfig`, `Tier`, `PipelineMode` defined before first use
- [x] Test isolation: all tests use `tmp_path`, monkeypatch, or mocks — no real API calls required
- [x] 286 existing tests: no modifications to existing modules except server.py import + 3 new tools
- [x] DEPTHFUSION_TIER_THRESHOLD: configurable via env var throughout
- [x] Knowledge graph: explicitly excluded from this plan
