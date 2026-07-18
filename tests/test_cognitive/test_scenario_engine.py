"""Tests for ScenarioEngine — E-68 S-230 T-794/T-795/T-796/T-797.

Covers:
  - AC-1: rebuild() clusters L1 memories by cosine+24h window into
          named scene blocks in scenarios-{project_id}.md
  - AC-2: rebuild() triggered after every PersonaEngine.generate()
  - AC-3: include_scenarios kwarg on recall injects scenario_summary
  - AC-4: scene block names distilled by DistillationClient;
          fallback to timestamp label when unavailable
  - Clustering by topic and time window
  - File output format
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from depthfusion.cognitive.scenario import (
    ScenarioEngine,
    _cluster_memories,
    _cosine,
    _distill_cluster_name,
    _project_id_from_scope,
    _token_jaccard,
    _within_time_window,
    get_scenario_engine,
    scenario_block_summary,
    scenarios_file_path,
)
from depthfusion.core.config import DepthFusionConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> DepthFusionConfig:
    return DepthFusionConfig(
        distillation_backend=kwargs.get("distillation_backend", "haiku"),
        persona_trigger_every_n=kwargs.get("persona_trigger_every_n", 50),
    )


def _make_memory(
    content: str,
    project_id: str = "test-proj",
    updated_at: datetime | None = None,
) -> MagicMock:
    """Return a mock MemoryObject."""
    m = MagicMock()
    m.content = content
    m.scope = MagicMock()
    m.scope.project_id = project_id
    m.updated_at = updated_at or datetime.now(tz=timezone.utc)
    return m


def _make_engine(
    config: DepthFusionConfig | None = None,
    *,
    complete_return: str = "Scene Alpha",
) -> tuple[ScenarioEngine, MagicMock]:
    cfg = config or _make_config()
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=complete_return)
    engine = ScenarioEngine(cfg, mock_client)
    return engine, mock_client


# ---------------------------------------------------------------------------
# _project_id_from_scope
# ---------------------------------------------------------------------------

def test_project_id_from_scope_uses_project_id():
    assert _project_id_from_scope({"project_id": "my-proj"}) == "my-proj"


def test_project_id_from_scope_falls_back_to_project():
    assert _project_id_from_scope({"project": "MyProj"}) == "myproj"


def test_project_id_from_scope_defaults():
    assert _project_id_from_scope({}) == "default"


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------

def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_empty_vectors():
    assert _cosine([], [1.0, 2.0]) == 0.0


def test_cosine_zero_norm():
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# _token_jaccard
# ---------------------------------------------------------------------------

def test_token_jaccard_identical():
    assert _token_jaccard("hello world", "hello world") == pytest.approx(1.0)


def test_token_jaccard_disjoint():
    assert _token_jaccard("hello world", "foo bar") == pytest.approx(0.0)


def test_token_jaccard_partial():
    result = _token_jaccard("hello world foo", "hello bar baz")
    assert 0.0 < result < 1.0


# ---------------------------------------------------------------------------
# _within_time_window
# ---------------------------------------------------------------------------

def test_within_time_window_same_time():
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("a", updated_at=now)
    m2 = _make_memory("b", updated_at=now)
    assert _within_time_window(m1, m2) is True


def test_within_time_window_23h_apart():
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("a", updated_at=now)
    m2 = _make_memory("b", updated_at=now - timedelta(hours=23))
    assert _within_time_window(m1, m2) is True


def test_outside_time_window_25h_apart():
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("a", updated_at=now)
    m2 = _make_memory("b", updated_at=now - timedelta(hours=25))
    assert _within_time_window(m1, m2) is False


# ---------------------------------------------------------------------------
# _cluster_memories — clustering by topic (via mocked embed_fn)
# ---------------------------------------------------------------------------

def test_cluster_memories_empty():
    result = _cluster_memories([], embed_fn=None)
    assert result == []


def test_cluster_memories_single():
    now = datetime.now(tz=timezone.utc)
    m = _make_memory("only one", updated_at=now)
    clusters = _cluster_memories([m], embed_fn=None)
    assert len(clusters) == 1
    assert clusters[0][0] is m


def test_cluster_memories_similar_by_jaccard():
    """Similar content + same time window → one cluster (Jaccard fallback)."""
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("python async await code pattern", updated_at=now)
    m2 = _make_memory("python async await code style", updated_at=now)
    clusters = _cluster_memories([m1, m2], embed_fn=None, cosine_threshold=0.3)
    # Both should be in the same cluster.
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_cluster_memories_different_topics_two_clusters():
    """Dissimilar content → two separate clusters."""
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("python async await", updated_at=now)
    m2 = _make_memory("kubernetes docker container", updated_at=now)
    clusters = _cluster_memories([m1, m2], embed_fn=None, cosine_threshold=0.8)
    assert len(clusters) == 2


def test_cluster_memories_time_window_separates_similar():
    """Similar content but > 24 h apart → two separate clusters."""
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(hours=30)
    m1 = _make_memory("python async await code pattern", updated_at=now)
    m2 = _make_memory("python async await code style", updated_at=old)
    clusters = _cluster_memories([m1, m2], embed_fn=None, cosine_threshold=0.3)
    # Time window gap breaks the cluster.
    assert len(clusters) == 2


def test_cluster_memories_uses_embed_fn():
    """When embed_fn returns vectors, clustering uses cosine similarity."""
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("topic A", updated_at=now)
    m2 = _make_memory("topic A variant", updated_at=now)
    m3 = _make_memory("completely different", updated_at=now)

    # m1 and m2 have similar vectors; m3 is orthogonal.
    def fake_embed(texts):
        vecs = []
        for t in texts:
            if "topic A" in t:
                vecs.append([1.0, 0.0, 0.0])
            else:
                vecs.append([0.0, 1.0, 0.0])
        return vecs

    clusters = _cluster_memories([m1, m2, m3], embed_fn=fake_embed, cosine_threshold=0.9)
    # m1+m2 should cluster; m3 separate.
    assert len(clusters) == 2
    cluster_sizes = sorted(len(c) for c in clusters)
    assert cluster_sizes == [1, 2]


def test_cluster_memories_embed_fn_failure_falls_back_to_jaccard():
    """When embed_fn raises, clustering falls back to Jaccard."""
    now = datetime.now(tz=timezone.utc)
    m1 = _make_memory("python async await code pattern", updated_at=now)
    m2 = _make_memory("python async await code style", updated_at=now)

    def bad_embed(texts):
        raise RuntimeError("embedding unavailable")

    # Should not raise; falls back to Jaccard.
    clusters = _cluster_memories([m1, m2], embed_fn=bad_embed, cosine_threshold=0.3)
    assert len(clusters) == 1


# ---------------------------------------------------------------------------
# AC-4: _distill_cluster_name — DistillationClient + timestamp fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_distill_cluster_name_uses_client():
    """_distill_cluster_name returns client's output when non-empty."""
    now = datetime.now(tz=timezone.utc)
    cluster = [_make_memory("async python patterns", updated_at=now)]
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value="Async Python Scene")

    name = await _distill_cluster_name(cluster, mock_client)
    assert name == "Async Python Scene"
    mock_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_distill_cluster_name_falls_back_when_client_none():
    """_distill_cluster_name returns timestamp label when client is None."""
    now = datetime(2024, 3, 15, 10, 30, tzinfo=timezone.utc)
    cluster = [_make_memory("some content", updated_at=now)]

    name = await _distill_cluster_name(cluster, None)
    assert "2024-03-15" in name or "Scene" in name


@pytest.mark.asyncio
async def test_distill_cluster_name_falls_back_on_empty_response():
    """_distill_cluster_name returns timestamp label when client returns empty."""
    now = datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc)
    cluster = [_make_memory("content", updated_at=now)]
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value="")

    name = await _distill_cluster_name(cluster, mock_client)
    assert name  # not empty — client returned "" so fallback timestamp label used
    assert "2024-06-01" in name or "Scene" in name


@pytest.mark.asyncio
async def test_distill_cluster_name_falls_back_on_client_exception():
    """_distill_cluster_name returns timestamp label when client raises."""
    now = datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc)
    cluster = [_make_memory("content", updated_at=now)]
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    name = await _distill_cluster_name(cluster, mock_client)
    assert name  # not empty — fallback was used


# ---------------------------------------------------------------------------
# AC-1: ScenarioEngine.rebuild() — file output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rebuild_writes_scenarios_file(tmp_path):
    """rebuild() writes scenarios-{project_id}.md to discoveries/."""
    engine, mock_client = _make_engine(complete_return="Python Async Scene")
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    now = datetime.now(tz=timezone.utc)
    memories = [
        _make_memory("python async await", updated_at=now),
        _make_memory("python async code", updated_at=now),
    ]

    with (
        patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries),
        patch.object(engine, "_load_l1_memories", return_value=memories),
        patch.object(engine, "_get_embed_fn", return_value=None),
    ):
        result_path = await engine.rebuild({"project_id": "myproj"})

    assert result_path == discoveries / "scenarios-myproj.md"
    assert result_path.exists()
    content = result_path.read_text()
    assert "scenarios" in content.lower() or "myproj" in content


@pytest.mark.asyncio
async def test_rebuild_creates_discoveries_dir_if_missing(tmp_path):
    """rebuild() creates the discoveries directory when it does not exist."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "does_not_exist" / "discoveries"

    with (
        patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries),
        patch.object(engine, "_load_l1_memories", return_value=[]),
        patch.object(engine, "_get_embed_fn", return_value=None),
    ):
        path = await engine.rebuild({"project_id": "newdir"})

    assert discoveries.exists()
    assert path.exists()


@pytest.mark.asyncio
async def test_rebuild_includes_frontmatter(tmp_path):
    """rebuild() includes YAML frontmatter in the output file."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()
    now = datetime.now(tz=timezone.utc)
    memories = [_make_memory("test content", updated_at=now)]

    with (
        patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries),
        patch.object(engine, "_load_l1_memories", return_value=memories),
        patch.object(engine, "_get_embed_fn", return_value=None),
    ):
        path = await engine.rebuild({"project_id": "fmtest"})

    content = path.read_text()
    assert "project: fmtest" in content
    assert "generated_at:" in content
    assert "scene_count:" in content


@pytest.mark.asyncio
async def test_rebuild_sets_last_rebuilt_at(tmp_path):
    """rebuild() sets last_rebuilt_at to a valid ISO timestamp."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    assert engine.last_rebuilt_at is None

    with (
        patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries),
        patch.object(engine, "_load_l1_memories", return_value=[]),
        patch.object(engine, "_get_embed_fn", return_value=None),
    ):
        await engine.rebuild({"project_id": "ts-test"})

    ts = engine.last_rebuilt_at
    assert ts is not None
    parsed = datetime.fromisoformat(ts)
    assert parsed is not None


@pytest.mark.asyncio
async def test_rebuild_with_empty_memories(tmp_path):
    """rebuild() with no memories writes an empty scenario file without error."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with (
        patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries),
        patch.object(engine, "_load_l1_memories", return_value=[]),
        patch.object(engine, "_get_embed_fn", return_value=None),
    ):
        path = await engine.rebuild({"project_id": "empty"})

    assert path.exists()
    content = path.read_text()
    assert "empty" in content


@pytest.mark.asyncio
async def test_rebuild_clusters_multiple_groups(tmp_path):
    """rebuild() produces separate scene blocks for dissimilar memories."""
    engine, mock_client = _make_engine(complete_return="Named Scene")
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    now = datetime.now(tz=timezone.utc)
    memories = [
        _make_memory("python async await", updated_at=now),
        _make_memory("python async code", updated_at=now),
        _make_memory("kubernetes docker container pods", updated_at=now),
        _make_memory("kubernetes helm deploy chart", updated_at=now),
    ]

    def fake_embed(texts):
        result = []
        for t in texts:
            if "python" in t:
                result.append([1.0, 0.0, 0.0])
            else:
                result.append([0.0, 1.0, 0.0])
        return result

    with (
        patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries),
        patch.object(engine, "_load_l1_memories", return_value=memories),
        patch.object(engine, "_get_embed_fn", return_value=fake_embed),
    ):
        path = await engine.rebuild({"project_id": "multicluster"})

    content = path.read_text()
    # Should have at least 2 scene blocks (H2 headings).
    h2_count = content.count("\n## ")
    assert h2_count >= 2


# ---------------------------------------------------------------------------
# AC-2: rebuild() triggered after PersonaEngine.generate()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persona_generate_triggers_scenario_rebuild(tmp_path):
    """PersonaEngine.generate() triggers ScenarioEngine.rebuild() as a post-pass."""
    from depthfusion.cognitive.persona import PersonaEngine
    from depthfusion.cognitive.scenario import ScenarioEngine

    cfg = _make_config()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    mock_persona_client = MagicMock()
    mock_persona_client.complete = AsyncMock(return_value="## Persona\n\nTest.")

    mock_scenario_engine = MagicMock(spec=ScenarioEngine)
    mock_scenario_engine.rebuild = AsyncMock()

    persona_engine = PersonaEngine(cfg, mock_persona_client)

    with (
        patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries),
        patch(
            "depthfusion.cognitive.scenario.get_scenario_engine",
            return_value=mock_scenario_engine,
        ),
    ):
        await persona_engine.generate({"project_id": "trigger-test"})

    # ScenarioEngine.rebuild() must have been called once.
    mock_scenario_engine.rebuild.assert_called_once()
    call_scope = mock_scenario_engine.rebuild.call_args[0][0]
    assert call_scope.get("project_id") == "trigger-test"


@pytest.mark.asyncio
async def test_persona_generate_scenario_failure_does_not_block(tmp_path):
    """PersonaEngine.generate() completes even when ScenarioEngine.rebuild() raises."""
    from depthfusion.cognitive.persona import PersonaEngine

    cfg = _make_config()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value="## Persona\n\nContent.")
    persona_engine = PersonaEngine(cfg, mock_client)

    mock_bad_engine = MagicMock()
    mock_bad_engine.rebuild = AsyncMock(side_effect=RuntimeError("Scenario boom"))

    with (
        patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries),
        patch(
            "depthfusion.cognitive.scenario.get_scenario_engine",
            return_value=mock_bad_engine,
        ),
    ):
        # Should not raise even though scenario engine fails.
        path = await persona_engine.generate({"project_id": "safe-test"})

    assert path.exists()


@pytest.mark.asyncio
async def test_persona_generate_no_scenario_engine_is_fine(tmp_path):
    """PersonaEngine.generate() works when no ScenarioEngine is registered."""
    from depthfusion.cognitive.persona import PersonaEngine

    cfg = _make_config()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value="## Persona\n\nOK.")
    persona_engine = PersonaEngine(cfg, mock_client)

    with (
        patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries),
        patch(
            "depthfusion.cognitive.scenario.get_scenario_engine",
            return_value=None,
        ),
    ):
        path = await persona_engine.generate({"project_id": "noengine"})

    assert path.exists()


# ---------------------------------------------------------------------------
# AC-3: include_scenarios kwarg on recall
# ---------------------------------------------------------------------------

def test_recall_include_scenarios_injects_summary(tmp_path):
    """_tool_recall with include_scenarios=True injects scenario_summary."""
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir(parents=True)

    scenarios_file = discoveries / "scenarios-testproj.md"
    scenarios_file.write_text(
        "---\nproject: testproj\ngenerated_at: 2024-01-01\nscene_count: 1\n---\n\n"
        "# Scenarios: testproj\n\n"
        "## Python Async Patterns\n\n"
        "*2 memories*\n\n"
        "- async await pattern\n"
        "- asyncio event loop\n",
        encoding="utf-8",
    )

    from depthfusion.mcp.tools.recall import _inject_scenario_summary

    base = json.dumps({
        "query": "async python",
        "blocks": [],
        "recall_id": "xyz",
        "total_sources_scanned": 0,
        "message": "ok",
        "strategy": "bm25-only",
        "hnsw_available": False,
    })

    with patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries):
        result = _inject_scenario_summary(
            base, {"project": "testproj", "query": "async python"}
        )
    data = json.loads(result)

    assert "scenario_summary" in data
    assert "Python Async" in data["scenario_summary"] or "Scene" in data["scenario_summary"]


def test_recall_include_scenarios_no_file_returns_unchanged(tmp_path):
    """When no scenarios file exists, response is returned unchanged."""
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir(parents=True)

    from depthfusion.mcp.tools.recall import _inject_scenario_summary

    base = json.dumps({"query": "test", "blocks": [], "recall_id": None})
    with patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries):
        result = _inject_scenario_summary(base, {"project": "nonexistent-proj"})
    data = json.loads(result)
    assert "scenario_summary" not in data


def test_recall_include_scenarios_with_matching_query(tmp_path):
    """scenario_summary returns most relevant block based on query terms."""
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir(parents=True)

    scenarios_file = discoveries / "scenarios-myproj.md"
    scenarios_file.write_text(
        "---\nproject: myproj\ngenerated_at: 2024-01-01\nscene_count: 2\n---\n\n"
        "# Scenarios: myproj\n\n"
        "## Kubernetes Deploy Scene\n\n"
        "*1 memories*\n\n"
        "- kubernetes helm deploy\n\n"
        "## Python Async Scene\n\n"
        "*1 memories*\n\n"
        "- python async await\n",
        encoding="utf-8",
    )

    with patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries):
        summary = scenario_block_summary("myproj", query="python async")
    assert summary is not None
    assert "Python Async" in summary


# ---------------------------------------------------------------------------
# scenario_block_summary helper
# ---------------------------------------------------------------------------

def test_scenario_block_summary_no_file_returns_none(tmp_path):
    """scenario_block_summary returns None when scenarios file does not exist."""
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()
    with patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries):
        result = scenario_block_summary("nonexistent")
    assert result is None


def test_scenario_block_summary_no_query_returns_first_block(tmp_path):
    """Without a query, scenario_block_summary returns the first block."""
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()
    (discoveries / "scenarios-proj.md").write_text(
        "---\nproject: proj\n---\n\n"
        "# Scenarios: proj\n\n"
        "## First Scene\n\n*1 memories*\n\n- content\n\n"
        "## Second Scene\n\n*1 memories*\n\n- other\n",
        encoding="utf-8",
    )

    with patch("depthfusion.cognitive.scenario._DISCOVERIES_DIR", discoveries):
        result = scenario_block_summary("proj", query="")

    assert result is not None
    assert "First Scene" in result


# ---------------------------------------------------------------------------
# scenarios_file_path helper
# ---------------------------------------------------------------------------

def test_scenarios_file_path_returns_expected_path():
    path = scenarios_file_path("myproject")
    assert path.name == "scenarios-myproject.md"
    assert "discoveries" in str(path)


# ---------------------------------------------------------------------------
# get_scenario_engine singleton
# ---------------------------------------------------------------------------

def test_get_scenario_engine_returns_none_before_init():
    """get_scenario_engine returns None when not yet initialised."""
    import depthfusion.cognitive.scenario as _mod
    original = _mod._scenario_engine
    try:
        _mod._scenario_engine = None
        result = get_scenario_engine()
        assert result is None
    finally:
        _mod._scenario_engine = original


def test_get_scenario_engine_initialises_with_config():
    """get_scenario_engine initialises singleton when config is provided."""
    import depthfusion.cognitive.scenario as _mod
    original = _mod._scenario_engine
    try:
        cfg = _make_config()
        engine = get_scenario_engine(config=cfg)
        assert engine is not None
        assert isinstance(engine, ScenarioEngine)
        # Calling again without args returns the singleton.
        same = get_scenario_engine()
        assert same is engine
    finally:
        _mod._scenario_engine = original
