"""Tests for PersonaEngine — E-68 S-229 T-789/T-790/T-791/T-792/T-793.

Covers:
  - generate() writes persona-{project_id}.md to discoveries/ (AC-1)
  - persona_trigger_every_n config + maybe_trigger threshold gate (AC-2)
  - recall include_persona prepends preamble (AC-4)
  - persona_last_updated + memory_count_at_last_generation exposed (AC-5)
  - DistillationClient integration via AsyncMock (AC-6)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from depthfusion.cognitive.persona import (
    PersonaEngine,
    _project_id_from_scope,
    persona_file_path,
)
from depthfusion.core.config import DepthFusionConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> DepthFusionConfig:
    """Return a minimal DepthFusionConfig with optional persona overrides."""
    return DepthFusionConfig(
        persona_trigger_every_n=kwargs.get("persona_trigger_every_n", 50),
        distillation_backend="haiku",
    )


def _make_engine(config=None, *, complete_return: str = "## Persona\n\nTest persona."):
    """Return a PersonaEngine with a mocked DistillationClient."""
    cfg = config or _make_config()
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=complete_return)
    return PersonaEngine(cfg, mock_client), mock_client


# ---------------------------------------------------------------------------
# _project_id_from_scope
# ---------------------------------------------------------------------------

def test_project_id_from_scope_uses_project_id_field():
    scope = {"project_id": "my-project"}
    assert _project_id_from_scope(scope) == "my-project"


def test_project_id_from_scope_falls_back_to_project():
    scope = {"project": "DepthFusion"}
    assert _project_id_from_scope(scope) == "depthfusion"


def test_project_id_from_scope_sanitises_special_chars():
    scope = {"project_id": "My Project/2024"}
    result = _project_id_from_scope(scope)
    assert "/" not in result
    assert " " not in result


def test_project_id_from_scope_defaults_to_default():
    assert _project_id_from_scope({}) == "default"


# ---------------------------------------------------------------------------
# AC-1: generate() writes persona-{project_id}.md
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_writes_persona_file(tmp_path):
    """generate() writes a persona-{project_id}.md to the discoveries dir."""
    engine, mock_client = _make_engine(complete_return="## Style\n\nBullet points.")
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        result_path = await engine.generate({"project_id": "myproj"})

    assert result_path == discoveries / "persona-myproj.md"
    assert result_path.exists()
    content = result_path.read_text()
    assert "## Style" in content
    assert "Bullet points." in content


@pytest.mark.asyncio
async def test_generate_includes_frontmatter(tmp_path):
    """generate() includes YAML frontmatter with project and generated_at."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        path = await engine.generate({"project_id": "alpha"})

    content = path.read_text()
    assert "project: alpha" in content
    assert "generated_at:" in content


@pytest.mark.asyncio
async def test_generate_writes_placeholder_when_llm_returns_empty(tmp_path):
    """generate() writes a placeholder when LLM returns empty string."""
    engine, mock_client = _make_engine(complete_return="")
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        path = await engine.generate({"project_id": "empty-proj"})

    content = path.read_text()
    assert "pending" in content.lower()


@pytest.mark.asyncio
async def test_generate_gracefully_handles_client_exception(tmp_path):
    """generate() writes a placeholder when DistillationClient raises."""
    cfg = _make_config()
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    engine = PersonaEngine(cfg, mock_client)

    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        path = await engine.generate({"project_id": "errproj"})

    content = path.read_text()
    assert path.exists()
    assert "errproj" in content or "pending" in content.lower()


@pytest.mark.asyncio
async def test_generate_creates_discoveries_dir_if_missing(tmp_path):
    """generate() creates the discoveries directory when it does not exist."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "does_not_exist" / "discoveries"

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        path = await engine.generate({"project_id": "newdir"})

    assert discoveries.exists()
    assert path.exists()


# ---------------------------------------------------------------------------
# AC-2: persona_trigger_every_n + maybe_trigger threshold gate
# ---------------------------------------------------------------------------

def test_maybe_trigger_fires_at_n_threshold(tmp_path):
    """maybe_trigger returns True and runs generate at the n-th memory."""
    cfg = _make_config(persona_trigger_every_n=50)
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    engine, mock_client = _make_engine(config=cfg)

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        result = engine.maybe_trigger({"project_id": "trig"}, new_count=50)

    assert result is True
    mock_client.complete.assert_called_once()


def test_maybe_trigger_does_not_fire_below_threshold():
    """maybe_trigger returns False when new_count < persona_trigger_every_n."""
    cfg = _make_config(persona_trigger_every_n=50)
    engine, mock_client = _make_engine(config=cfg)

    result = engine.maybe_trigger({"project_id": "notrig"}, new_count=49)

    assert result is False
    mock_client.complete.assert_not_called()


def test_maybe_trigger_fires_at_multiples_of_n(tmp_path):
    """maybe_trigger fires at 50, 100, 150 — not between."""
    cfg = _make_config(persona_trigger_every_n=50)
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    engine, mock_client = _make_engine(config=cfg)

    fire_counts = []
    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        for i in range(1, 201):
            fired = engine.maybe_trigger({"project_id": "mproj"}, new_count=i)
            if fired:
                fire_counts.append(i)

    # Should fire at 50, 100, 150, 200 (bucket boundaries).
    assert fire_counts == [50, 100, 150, 200]


def test_maybe_trigger_no_double_fire_at_same_count(tmp_path):
    """Calling maybe_trigger twice with the same new_count only fires once."""
    cfg = _make_config(persona_trigger_every_n=10)
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    engine, mock_client = _make_engine(config=cfg)

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        r1 = engine.maybe_trigger({"project_id": "p"}, new_count=10)
        r2 = engine.maybe_trigger({"project_id": "p"}, new_count=10)

    assert r1 is True
    assert r2 is False
    assert mock_client.complete.call_count == 1


def test_maybe_trigger_returns_false_when_n_is_zero():
    """maybe_trigger returns False (disabled) when persona_trigger_every_n=0."""
    cfg = _make_config(persona_trigger_every_n=0)
    engine, mock_client = _make_engine(config=cfg)

    result = engine.maybe_trigger({"project_id": "p"}, new_count=999)

    assert result is False
    mock_client.complete.assert_not_called()


# ---------------------------------------------------------------------------
# AC-5: persona_last_updated and memory_count_at_last_generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persona_last_updated_after_generate(tmp_path):
    """persona_last_updated is set to an ISO timestamp after generate()."""
    engine, _ = _make_engine()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    assert engine.persona_last_updated is None

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        await engine.generate({"project_id": "ts-test"})

    ts = engine.persona_last_updated
    assert ts is not None
    # Should be a valid ISO timestamp
    from datetime import datetime
    parsed = datetime.fromisoformat(ts)
    assert parsed is not None


def test_memory_count_at_last_generation_set_by_maybe_trigger(tmp_path):
    """memory_count_at_last_generation is set when maybe_trigger fires."""
    cfg = _make_config(persona_trigger_every_n=5)
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    engine, _ = _make_engine(config=cfg)

    assert engine.memory_count_at_last_generation is None

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        engine.maybe_trigger({"project_id": "cnt"}, new_count=5)

    assert engine.memory_count_at_last_generation == 5


# ---------------------------------------------------------------------------
# AC-4: include_persona prepends persona preamble in recall
# ---------------------------------------------------------------------------

def test_recall_include_persona_prepends_preamble(tmp_path, monkeypatch):
    """_tool_recall with include_persona=True prepends persona_preamble."""
    # Write a fake persona file under a patched home.
    discoveries = tmp_path / ".claude" / "shared" / "discoveries"
    discoveries.mkdir(parents=True)
    persona_file = discoveries / "persona-testproj.md"
    persona_file.write_text("# Persona: testproj\n\nTest persona content.", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from depthfusion.mcp.tools.recall import _prepend_persona_preamble

    base = json.dumps({
        "query": "test",
        "blocks": [],
        "recall_id": "abc123",
        "total_sources_scanned": 0,
        "message": "test",
        "strategy": "bm25-only",
        "hnsw_available": False,
    })

    result = _prepend_persona_preamble(base, {"project": "testproj"})
    data = json.loads(result)

    assert "persona_preamble" in data
    assert "Test persona content." in data["persona_preamble"]


def test_recall_include_persona_with_real_fs(tmp_path, monkeypatch):
    """include_persona prepends persona_preamble using real filesystem patch."""
    discoveries = tmp_path / ".claude" / "shared" / "discoveries"
    discoveries.mkdir(parents=True)
    persona_file = discoveries / "persona-depthfusion.md"
    persona_file.write_text("# Persona: depthfusion\n\nPersona content.", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from depthfusion.mcp.tools.recall import _prepend_persona_preamble

    base = json.dumps({
        "query": "q",
        "blocks": [{"chunk_id": "c1", "source": "discovery", "score": 0.9, "snippet": "x"}],
        "recall_id": "r1",
        "total_sources_scanned": 1,
        "message": "ok",
        "strategy": "bm25-only",
        "hnsw_available": False,
    })

    result = _prepend_persona_preamble(base, {"project": "depthfusion"})
    data = json.loads(result)

    assert "persona_preamble" in data
    assert "Persona: depthfusion" in data["persona_preamble"]
    # persona_preamble should be first key
    keys = list(data.keys())
    assert keys[0] == "persona_preamble"


def test_recall_include_persona_no_file_returns_unchanged(tmp_path, monkeypatch):
    """When persona file doesn't exist, response is returned unchanged."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".claude" / "shared" / "discoveries").mkdir(parents=True)

    from depthfusion.mcp.tools.recall import _prepend_persona_preamble

    base = json.dumps({"query": "q", "blocks": [], "recall_id": None})
    result = _prepend_persona_preamble(base, {"project": "nonexistent-proj"})

    data = json.loads(result)
    assert "persona_preamble" not in data


# ---------------------------------------------------------------------------
# DistillationClient integration — async boundary handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_distillation_client_complete_is_awaited(tmp_path):
    """PersonaEngine.generate() awaits DistillationClient.complete()."""
    cfg = _make_config()
    call_log: list[str] = []

    async def fake_complete(prompt: str, *, max_tokens: int = 512) -> str:
        call_log.append(prompt)
        return "## Result\n\nGenerated."

    mock_client = MagicMock()
    mock_client.complete = fake_complete
    engine = PersonaEngine(cfg, mock_client)

    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        await engine.generate({"project_id": "async-test"})

    assert len(call_log) == 1
    assert "async-test" in call_log[0]


@pytest.mark.asyncio
async def test_generate_calls_complete_with_project_in_prompt(tmp_path):
    """generate() passes the project_id in the prompt to the distillation client."""
    engine, mock_client = _make_engine()
    discoveries = tmp_path / "discoveries"
    discoveries.mkdir()

    with patch("depthfusion.cognitive.persona._DISCOVERIES_DIR", discoveries):
        await engine.generate({"project_id": "foobar-project"})

    call_args = mock_client.complete.call_args
    assert call_args is not None
    prompt = call_args[0][0]  # positional first arg
    assert "foobar-project" in prompt


# ---------------------------------------------------------------------------
# Config field: persona_trigger_every_n
# ---------------------------------------------------------------------------

def test_config_persona_trigger_every_n_default():
    """DepthFusionConfig.persona_trigger_every_n defaults to 50."""
    cfg = DepthFusionConfig()
    assert cfg.persona_trigger_every_n == 50


def test_config_persona_trigger_every_n_from_env(monkeypatch):
    """persona_trigger_every_n can be overridden via DEPTHFUSION_PERSONA_TRIGGER_EVERY_N."""
    monkeypatch.setenv("DEPTHFUSION_PERSONA_TRIGGER_EVERY_N", "25")
    cfg = DepthFusionConfig.from_env()
    assert cfg.persona_trigger_every_n == 25


def test_config_persona_trigger_every_n_custom():
    """DepthFusionConfig accepts persona_trigger_every_n as a constructor arg."""
    cfg = DepthFusionConfig(persona_trigger_every_n=100)
    assert cfg.persona_trigger_every_n == 100


# ---------------------------------------------------------------------------
# persona_file_path helper
# ---------------------------------------------------------------------------

def test_persona_file_path_returns_expected_path():
    path = persona_file_path("myproject")
    assert path.name == "persona-myproject.md"
    assert "discoveries" in str(path)
