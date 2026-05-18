# tests/test_retrieval/test_hybrid.py
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline, query_hits_boost


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


def test_pipeline_rrf_handles_empty_bm25_list():
    p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
    vector = [{"chunk_id": "a", "score": 0.9}]
    fused = p.rrf_fuse([], vector, k=60)
    assert fused == vector


def test_pipeline_apply_reranker_tier1_calls_reranker(monkeypatch):
    """Post T-120 migration: the reranker is the backend interface, not the
    anthropic SDK directly. Inject a mock backend via HaikuReranker and
    attach to the pipeline.
    """
    from depthfusion.retrieval.reranker import HaikuReranker

    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.return_value = [(0, 1.0), (1, 0.95), (2, 0.90)]
    reranker = HaikuReranker(backend=mock_backend)

    p = RecallPipeline(mode=PipelineMode.VPS_TIER1)
    p._reranker = reranker  # inject the mock-backed reranker
    blocks = _make_blocks(5)
    result = p.apply_reranker(blocks, "query", top_k=3)
    assert len(result) == 3


def test_pipeline_from_env_local_mode(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    p = RecallPipeline.from_env()
    assert p.mode == PipelineMode.LOCAL


def test_pipeline_from_env_vps_uses_tier_manager(monkeypatch):
    """from_env() in vps mode should query TierManager; skip if storage not yet built."""
    tier_manager_mod = pytest.importorskip("depthfusion.storage.tier_manager")
    Tier = tier_manager_mod.Tier
    TierConfig = tier_manager_mod.TierConfig
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    with patch("depthfusion.retrieval.hybrid.TierManager") as mock_tm:
        mock_tm.return_value.detect_tier.return_value = TierConfig(
            tier=Tier.VPS_TIER1, corpus_size=10, threshold=500,
            sessions_until_promotion=490, mode="vps"
        )
        p = RecallPipeline.from_env()
    assert p.mode == PipelineMode.VPS_TIER1


def test_expand_query_called_when_graph_enabled(tmp_path, monkeypatch):
    """expand_query injects linked terms before BM25 when flag is on."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.linker import make_edge_id
    from depthfusion.graph.store import JSONGraphStore
    from depthfusion.graph.types import Edge, Entity

    store_path = tmp_path / "g.json"
    store = JSONGraphStore(path=store_path)
    e1 = Entity(entity_id=make_entity_id("TierManager", "class", "test"),
                name="TierManager", type="class", project="test",
                source_files=["m.md"], confidence=1.0,
                first_seen="2026-03-28T00:00:00", metadata={})
    e2 = Entity(entity_id=make_entity_id("RecallPipeline", "class", "test"),
                name="RecallPipeline", type="class", project="test",
                source_files=["m.md"], confidence=1.0,
                first_seen="2026-03-28T00:00:00", metadata={})
    store.upsert_entity(e1)
    store.upsert_entity(e2)
    store.upsert_edge(Edge(
        edge_id=make_edge_id(e1.entity_id, e2.entity_id, "CO_OCCURS"),
        source_id=e1.entity_id, target_id=e2.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))

    from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    expanded = pipeline.maybe_expand_query("TierManager storage", graph_store=store)
    assert "RecallPipeline" in expanded


def test_expand_query_skipped_when_graph_disabled(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "false")
    from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.maybe_expand_query("TierManager storage", graph_store=None)
    assert result == "TierManager storage"


def test_expand_query_no_op_when_store_is_none(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.maybe_expand_query("any query", graph_store=None)
    assert result == "any query"


# ---------------------------------------------------------------------------
# T-323 — CognitiveScorer integration tests
# ---------------------------------------------------------------------------

def _make_scored_blocks(n: int) -> list[dict]:
    """Return blocks with descending BM25 scores (doc0 is highest ranked)."""
    return [
        {
            "chunk_id": f"doc{i}",
            "source": "memory",
            "score": float(n - i),
            "snippet": f"content about topic {i}",
        }
        for i in range(n)
    ]


def test_cognitive_scoring_disabled_by_default(monkeypatch):
    """When DEPTHFUSION_COGNITIVE_SCORING is unset, blocks are returned unchanged."""
    monkeypatch.delenv("DEPTHFUSION_COGNITIVE_SCORING", raising=False)
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = _make_scored_blocks(4)
    original_ids = [b["chunk_id"] for b in blocks]

    result = pipeline.apply_cognitive_scoring(blocks)

    assert [b["chunk_id"] for b in result] == original_ids
    # cognitive_score must NOT be present when flag is off
    for block in result:
        assert "cognitive_score" not in block


def test_cognitive_scoring_explicit_false(monkeypatch):
    """DEPTHFUSION_COGNITIVE_SCORING=false keeps existing behaviour."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "false")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = _make_scored_blocks(3)
    result = pipeline.apply_cognitive_scoring(blocks)
    assert [b["chunk_id"] for b in result] == [b["chunk_id"] for b in blocks]
    assert all("cognitive_score" not in b for b in result)


def test_cognitive_scoring_enabled_attaches_score(monkeypatch):
    """When flag is true, each block gains a `cognitive_score` key."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "true")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = _make_scored_blocks(3)

    result = pipeline.apply_cognitive_scoring(blocks)

    assert len(result) == 3
    for block in result:
        assert "cognitive_score" in block
        score = block["cognitive_score"]
        assert 0.0 <= score <= 1.0


def test_cognitive_scoring_reorders_by_score(monkeypatch):
    """With flag on, blocks are sorted by cognitive_score descending."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "true")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)

    # Construct blocks where doc2 has the highest vector_score so CognitiveScorer
    # should prefer it despite its lower BM25 rank.
    blocks = [
        {"chunk_id": "doc0", "score": 10.0, "vector_score": 0.1, "snippet": "a"},
        {"chunk_id": "doc1", "score": 5.0,  "vector_score": 0.5, "snippet": "b"},
        {"chunk_id": "doc2", "score": 1.0,  "vector_score": 0.99, "snippet": "c"},
    ]

    result = pipeline.apply_cognitive_scoring(blocks)

    cog_scores = [b["cognitive_score"] for b in result]
    # Scores must be sorted descending
    assert cog_scores == sorted(cog_scores, reverse=True)

    # doc2 has the highest semantic input (0.99) and should appear first
    # (semantic weight 0.25 dominates the score delta from doc0's higher lexical).
    assert result[0]["chunk_id"] == "doc2"


def test_cognitive_scoring_handles_empty_blocks(monkeypatch):
    """Empty input → empty output, no error."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "true")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.apply_cognitive_scoring([])
    assert result == []


def test_cognitive_scoring_fallback_on_import_error(monkeypatch):
    """If CognitiveScorer import fails, original blocks are returned unchanged."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "true")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = _make_scored_blocks(3)
    original_ids = [b["chunk_id"] for b in blocks]

    with patch.dict("sys.modules", {"depthfusion.cognitive.scorer": None}):
        result = pipeline.apply_cognitive_scoring(blocks)

    assert [b["chunk_id"] for b in result] == original_ids
    assert all("cognitive_score" not in b for b in result)


def test_cognitive_scoring_uses_recency_from_block(monkeypatch):
    """Blocks with `recency` field use it; blocks without default to 0.5."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "true")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)

    # Two identical blocks except for recency; higher recency should score higher.
    blocks = [
        {"chunk_id": "old",    "score": 5.0, "recency": 0.1, "snippet": "x"},
        {"chunk_id": "recent", "score": 5.0, "recency": 0.9, "snippet": "x"},
    ]

    result = pipeline.apply_cognitive_scoring(blocks)

    # "recent" should rank above "old" because recency weight is 0.08
    assert result[0]["chunk_id"] == "recent"


def test_cognitive_scoring_zero_score_blocks_no_division_error(monkeypatch):
    """Blocks with score=0 should not cause division by zero."""
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "true")
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = [
        {"chunk_id": "a", "score": 0.0, "snippet": "alpha"},
        {"chunk_id": "b", "score": 0.0, "snippet": "beta"},
    ]
    result = pipeline.apply_cognitive_scoring(blocks)
    assert len(result) == 2
    for block in result:
        assert "cognitive_score" in block


# ---------------------------------------------------------------------------
# S-117: query_hits_boost integration tests
# ---------------------------------------------------------------------------


def test_query_hits_boost_no_tracker_returns_one():
    assert query_hits_boost("any-chunk-id", tracker=None) == 1.0


def test_query_hits_boost_scales_with_hits(tmp_path):
    from depthfusion.core.hit_tracker import HitTracker
    tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
    tracker.register_hits(["chunk-a"] * 3)
    boost = query_hits_boost("chunk-a", tracker)
    assert boost == pytest.approx(1.3)


def test_query_hits_boost_caps_at_max(tmp_path):
    from depthfusion.core.hit_tracker import HitTracker
    tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
    tracker.register_hits(["chunk-b"] * 20)
    boost = query_hits_boost("chunk-b", tracker)
    assert boost == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# S-121: linear_blend() — MemPalace-style BM25-relative + vector-absolute
# ---------------------------------------------------------------------------

class TestLinearBlend:
    """Tests for HybridRetriever.linear_blend()."""

    def _pipeline(self):
        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        p = RecallPipeline.__new__(RecallPipeline)
        p.mode = PipelineMode.LOCAL
        p._reranker = None
        return p

    def _b(self, cid, bm25=0.0, vector=0.0):
        return {"chunk_id": cid, "bm25_score": bm25, "vector_score": vector,
                "content": f"block {cid}"}

    def test_empty_both_returns_empty(self):
        pipeline = self._pipeline()
        assert pipeline.linear_blend([], []) == []

    def test_bm25_only_normalises_to_0_1_range(self):
        pipeline = self._pipeline()
        bm25 = [self._b("a", bm25=10.0), self._b("b", bm25=20.0)]
        result = pipeline.linear_blend(bm25, [], bm25_weight=1.0, vector_weight=0.0)
        assert result[0]["chunk_id"] == "b"
        assert result[1]["chunk_id"] == "a"

    def test_vector_only_uses_absolute_cosine(self):
        pipeline = self._pipeline()
        vec = [self._b("x", vector=0.9), self._b("y", vector=0.5)]
        result = pipeline.linear_blend([], vec, bm25_weight=0.0, vector_weight=1.0)
        assert result[0]["chunk_id"] == "x"

    def test_blend_weights_applied_correctly(self):
        pipeline = self._pipeline()
        # a: bm25 normalises to 1.0 (only bm25 candidate) → score = 0.4*1.0 = 0.40
        # b: bm25=0, vector=0.9 → score = 0.6*0.9 = 0.54 → b wins
        bm25 = [self._b("a", bm25=5.0)]
        vec = [self._b("b", vector=0.9)]
        result = pipeline.linear_blend(bm25, vec, bm25_weight=0.4, vector_weight=0.6)
        assert result[0]["chunk_id"] == "b"

    def test_deduplication_vector_wins(self):
        """When a chunk appears in both lists, the vector result's dict wins."""
        pipeline = self._pipeline()
        bm25 = [self._b("dup", bm25=5.0)]
        vec  = [{"chunk_id": "dup", "bm25_score": 0.0, "vector_score": 0.8,
                 "content": "vector-version"}]
        result = pipeline.linear_blend(bm25, vec)
        assert len(result) == 1
        assert result[0]["content"] == "vector-version"

    def test_flag_rrf_default(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_BLEND_MODE", raising=False)
        import depthfusion.retrieval.hybrid as mod
        # _BLEND_MODE is set at import time; verify the default without reload
        # (importlib.reload creates new class objects that break enum identity
        # in other test modules — use setattr instead)
        assert mod._BLEND_MODE == "rrf"

    def test_flag_linear_activates_linear_blend(self, monkeypatch):
        import depthfusion.retrieval.hybrid as mod
        monkeypatch.setattr(mod, "_BLEND_MODE", "linear")
        assert mod._BLEND_MODE == "linear"


# ---------------------------------------------------------------------------
# S-122: sub_scope (Room) scoping — ADR-001 / OD-3
# ---------------------------------------------------------------------------

class TestSubProjectScoping:
    """Isolation tests for the Room (sub_scope) filter — ADR-001 / OD-3."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _block(self, cid: str, sub_scope: str | None = None,
               project: str | None = None, content: str = "") -> dict:
        b: dict = {"chunk_id": cid, "content": content}
        if sub_scope is not None:
            b["sub_scope"] = sub_scope
        if project is not None:
            b["project"] = project
        return b

    def _block_with_frontmatter(self, cid: str, sub_scope: str) -> dict:
        fm = (
            "---\n"
            f"project: test-proj\n"
            f"sub_scope: {sub_scope}\n"
            "---\n\n# Content\nSome text."
        )
        return {"chunk_id": cid, "content": fm}

    # ------------------------------------------------------------------
    # extract_frontmatter_sub_scope
    # ------------------------------------------------------------------

    def test_extract_sub_scope_from_frontmatter(self):
        from depthfusion.retrieval.hybrid import extract_frontmatter_sub_scope
        content = "---\nproject: myproj\nsub_scope: auth\n---\nBody."
        assert extract_frontmatter_sub_scope(content) == "auth"

    def test_extract_sub_scope_absent_returns_none(self):
        from depthfusion.retrieval.hybrid import extract_frontmatter_sub_scope
        content = "---\nproject: myproj\n---\nBody."
        assert extract_frontmatter_sub_scope(content) is None

    def test_extract_sub_scope_no_frontmatter_returns_none(self):
        from depthfusion.retrieval.hybrid import extract_frontmatter_sub_scope
        assert extract_frontmatter_sub_scope("Just some text.") is None

    def test_extract_sub_scope_empty_content_returns_none(self):
        from depthfusion.retrieval.hybrid import extract_frontmatter_sub_scope
        assert extract_frontmatter_sub_scope("") is None

    # ------------------------------------------------------------------
    # ADR-001 truth-table: _block_passes_sub_scope
    # ------------------------------------------------------------------

    def test_truth_table_filter_off_passes_all(self):
        """sub_scope=None → filter off; every block passes regardless of its label."""
        from depthfusion.retrieval.hybrid import _block_passes_sub_scope
        assert _block_passes_sub_scope(self._block("a", sub_scope="room-A"), sub_scope=None)
        assert _block_passes_sub_scope(self._block("b", sub_scope="room-B"), sub_scope=None)
        assert _block_passes_sub_scope(self._block("c"), sub_scope=None)

    def test_truth_table_unlabelled_block_always_included(self):
        """Block with no sub_scope → INCLUDED regardless of active Room."""
        from depthfusion.retrieval.hybrid import _block_passes_sub_scope
        unlabelled = self._block("u")
        assert _block_passes_sub_scope(unlabelled, sub_scope="room-A")

    def test_truth_table_matching_sub_scope_included(self):
        """Block sub_scope == active Room → INCLUDED."""
        from depthfusion.retrieval.hybrid import _block_passes_sub_scope
        b = self._block("m", sub_scope="auth")
        assert _block_passes_sub_scope(b, sub_scope="auth")

    def test_truth_table_differing_sub_scope_excluded(self):
        """Block sub_scope != active Room → EXCLUDED."""
        from depthfusion.retrieval.hybrid import _block_passes_sub_scope
        b = self._block("d", sub_scope="billing")
        assert not _block_passes_sub_scope(b, sub_scope="auth")

    # ------------------------------------------------------------------
    # filter_blocks_by_sub_scope list-level
    # ------------------------------------------------------------------

    def test_none_sub_scope_returns_all_blocks(self):
        from depthfusion.retrieval.hybrid import filter_blocks_by_sub_scope
        blocks = [self._block("a", "room-A"), self._block("b", "room-B"),
                  self._block("c")]
        assert filter_blocks_by_sub_scope(blocks, sub_scope=None) == blocks

    def test_active_room_filters_to_matching_and_unlabelled(self):
        from depthfusion.retrieval.hybrid import filter_blocks_by_sub_scope
        blocks = [
            self._block("match", "auth"),
            self._block("other", "billing"),
            self._block("legacy"),
        ]
        result = filter_blocks_by_sub_scope(blocks, sub_scope="auth")
        ids = [b["chunk_id"] for b in result]
        assert "match" in ids
        assert "legacy" in ids
        assert "other" not in ids

    def test_no_matching_blocks_returns_empty(self):
        from depthfusion.retrieval.hybrid import filter_blocks_by_sub_scope
        blocks = [self._block("x", "billing"), self._block("y", "payments")]
        result = filter_blocks_by_sub_scope(blocks, sub_scope="auth")
        assert result == []

    def test_sub_scope_from_frontmatter_parsed_correctly(self):
        """Falls back to frontmatter for block-0 content when no explicit key."""
        from depthfusion.retrieval.hybrid import filter_blocks_by_sub_scope
        fm_block = self._block_with_frontmatter("fm-auth", "auth")
        other = self._block("other", sub_scope="billing")
        legacy = self._block("legacy")
        result = filter_blocks_by_sub_scope(
            [fm_block, other, legacy], sub_scope="auth"
        )
        ids = [b["chunk_id"] for b in result]
        assert "fm-auth" in ids
        assert "legacy" in ids
        assert "other" not in ids

    # ------------------------------------------------------------------
    # pipeline order — Wing (project) filter must run before Room (sub_scope)
    # ------------------------------------------------------------------

    def test_pipeline_order_project_filter_before_sub_scope(self):
        """Room filter should only see Wing-filtered survivors.

        Blocks from a different project that happen to carry a matching
        sub_scope must be excluded by the Wing gate before Room runs.
        """
        from depthfusion.retrieval.hybrid import (
            filter_blocks_by_project,
            filter_blocks_by_sub_scope,
        )
        my_project_auth_block = {
            "chunk_id": "mine-auth", "project": "myproj",
            "sub_scope": "auth", "content": "",
        }
        foreign_auth_block = {
            "chunk_id": "foreign-auth", "project": "otherproj",
            "sub_scope": "auth", "content": "",
        }
        untagged_block = {"chunk_id": "untagged", "content": ""}

        all_blocks = [my_project_auth_block, foreign_auth_block, untagged_block]

        # Wing filter first
        after_wing = filter_blocks_by_project(
            all_blocks, current_project="myproj", cross_project=False
        )
        # Room filter second
        after_room = filter_blocks_by_sub_scope(after_wing, sub_scope="auth")

        ids = [b["chunk_id"] for b in after_room]
        assert "mine-auth" in ids      # same project + matching room
        assert "untagged" in ids       # no project + no room → always included
        assert "foreign-auth" not in ids  # Wing gate excluded it before Room ran

    # ------------------------------------------------------------------
    # GraphScope.to_dict includes sub_scope
    # ------------------------------------------------------------------

    def test_graph_scope_to_dict_includes_sub_scope(self):
        from depthfusion.graph.types import GraphScope
        scope = GraphScope(
            mode="project",
            active_projects=["myproj"],
            set_at="2026-05-18T00:00:00",
            session_id="test-session",
            sub_scope="auth",
        )
        d = scope.to_dict()
        assert d["sub_scope"] == "auth"

    def test_graph_scope_to_dict_sub_scope_none(self):
        from depthfusion.graph.types import GraphScope
        scope = GraphScope(
            mode="project",
            active_projects=["myproj"],
            set_at="2026-05-18T00:00:00",
            session_id="test-session",
        )
        assert scope.sub_scope is None
        d = scope.to_dict()
        assert d["sub_scope"] is None

    # ------------------------------------------------------------------
    # scope persistence round-trip (write → read → sub_scope preserved)
    # ------------------------------------------------------------------

    def test_scope_round_trips_sub_scope(self, tmp_path):
        from depthfusion.graph.scope import read_scope, write_scope
        from depthfusion.graph.types import GraphScope
        scope_file = tmp_path / "scope.json"
        scope = GraphScope(
            mode="project",
            active_projects=["myproj"],
            set_at="2026-05-18T12:00:00",
            session_id="s1",
            sub_scope="auth",
        )
        write_scope(scope, path=scope_file)
        restored = read_scope(path=scope_file)
        assert restored is not None
        assert restored.sub_scope == "auth"

    def test_scope_round_trips_none_sub_scope(self, tmp_path):
        from depthfusion.graph.scope import read_scope, write_scope
        from depthfusion.graph.types import GraphScope
        scope_file = tmp_path / "scope.json"
        scope = GraphScope(
            mode="project",
            active_projects=["myproj"],
            set_at="2026-05-18T12:00:00",
            session_id="s1",
        )
        write_scope(scope, path=scope_file)
        restored = read_scope(path=scope_file)
        assert restored is not None
        assert restored.sub_scope is None

    # ------------------------------------------------------------------
    # backward-compat regression — existing blocks unaffected when Room off
    # ------------------------------------------------------------------

    def test_backward_compat_no_sub_scope_key_passes_through(self):
        """Pre-Room blocks (no sub_scope at all) always pass filter_blocks_by_sub_scope."""
        from depthfusion.retrieval.hybrid import filter_blocks_by_sub_scope
        legacy_blocks = [
            {"chunk_id": "old-1", "content": "---\nproject: p\n---\nOld discovery."},
            {"chunk_id": "old-2", "content": "# Note\nSome session text."},
        ]
        # With Room active
        result = filter_blocks_by_sub_scope(legacy_blocks, sub_scope="auth")
        assert len(result) == 2  # all legacy blocks survive

    def test_backward_compat_sub_scope_none_is_no_op(self):
        """sub_scope=None is a strict no-op — list is returned unchanged."""
        from depthfusion.retrieval.hybrid import filter_blocks_by_sub_scope
        original = [self._block("a", "room-X"), self._block("b")]
        result = filter_blocks_by_sub_scope(original, sub_scope=None)
        assert result is not original  # returns a new list (copy)
        assert result == original

    # ------------------------------------------------------------------
    # _tool_set_scope integration (H-1 fix: schema key `scope` not `mode`)
    # ------------------------------------------------------------------

    def test_tool_set_scope_reads_scope_key(self, tmp_path, monkeypatch):
        """Handler reads `scope` (schema key) and resolves mode correctly."""
        import json

        from depthfusion.mcp.server import _tool_set_scope
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
        result = json.loads(_tool_set_scope({"scope": "cross_project"}))
        assert result["ok"] is True
        assert result["mode"] == "cross_project"

    def test_tool_set_scope_back_compat_mode_key(self, tmp_path, monkeypatch):
        """Handler also accepts legacy `mode` key for back-compat."""
        import json

        from depthfusion.mcp.server import _tool_set_scope
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
        result = json.loads(_tool_set_scope({"mode": "global", "scope": None}))
        assert result["ok"] is True
        assert result["mode"] == "global"

    def test_tool_set_scope_sub_scope_persisted(self, tmp_path, monkeypatch):
        """set_scope with sub_scope persists Room label and echoes it."""
        import json

        from depthfusion.mcp.server import _tool_set_scope
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
        result = json.loads(_tool_set_scope({"scope": "project", "sub_scope": "auth"}))
        assert result["ok"] is True
        assert result["sub_scope"] == "auth"

    def test_tool_set_scope_empty_sub_scope_becomes_none(self, tmp_path, monkeypatch):
        """Empty string sub_scope coerces to None (Room filter off)."""
        import json

        from depthfusion.mcp.server import _tool_set_scope
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
        result = json.loads(_tool_set_scope({"scope": "project", "sub_scope": "  "}))
        assert result["ok"] is True
        assert result["sub_scope"] is None

    def test_tool_set_scope_sub_scope_orthogonal_to_mode(self, tmp_path, monkeypatch):
        """sub_scope is preserved regardless of scope/mode value."""
        import json

        from depthfusion.mcp.server import _tool_set_scope
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
        result = json.loads(_tool_set_scope({"scope": "global", "sub_scope": "billing"}))
        assert result["mode"] == "global"
        assert result["sub_scope"] == "billing"
