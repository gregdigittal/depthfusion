"""Tests for S-92 — explain=True parameter on depthfusion_recall_relevant.

Verifies that:
1. explain=False (default) produces no 'explain' key in any block
2. explain=True attaches a structured explain block to each result
3. explain block contains bm25_score (float) and reranker_rank (int)
4. explain block never includes env var values or sensitive data
5. explain=True does not change the 'score' or 'snippet' fields
6. When query is empty (recency mode), no explain key is present
7. Schema includes the explain property with correct type/default
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from depthfusion.mcp.server import _make_tool_schema, _tool_recall_impl

# ── Fixture corpus (same pattern as test_v04_output_identity) ────────────


_MEMORY_FILES = {
    "preferences.md": (
        "# Preferences\n\n"
        "## Coding style\n"
        "Use tabs for indentation. Prefer const over let. No any types.\n\n"
        "## Testing\n"
        "Write tests before implementation. TDD cycle. Red-green-refactor.\n"
    ),
    "architecture.md": (
        "# Architecture\n\n"
        "## Backend provider interface\n"
        "The backend provider abstracts LLM backends behind a typed protocol. "
        "Pluggable implementations for Haiku, Gemma, and Null backends.\n\n"
        "## Design principles\n"
        "Pluggable. Typed errors. Graceful degradation. No vendor lock-in.\n"
    ),
}

_DISCOVERY_FILES = {
    "2026-04-10-backend-design.md": (
        "# Backend Design Discovery\n\n"
        "## Context\n"
        "Designing the backend provider interface for v0.5. Provider pattern.\n\n"
        "## Decision\n"
        "Use a Protocol class with runtime_checkable. Four typed error classes.\n"
    ),
}

_SESSION_FILES = {
    "2026-04-15-session.tmp": (
        "# Session\n\n"
        "## Work\n"
        "Implemented the protocol. Tests pass. Backend factory dispatches correctly.\n"
    ),
}


@pytest.fixture
def fixture_home(tmp_path, monkeypatch):
    """Build source directories under tmp_path, monkeypatch Path.home()."""
    memory_dir = tmp_path / ".claude" / "projects" / "-home-gregmorris" / "memory"
    discoveries_dir = tmp_path / ".claude" / "shared" / "discoveries"
    sessions_dir = tmp_path / ".claude" / "sessions"
    for d in (memory_dir, discoveries_dir, sessions_dir):
        d.mkdir(parents=True, exist_ok=True)

    base_ts = 1700000000.0
    for i, (name, content) in enumerate(_MEMORY_FILES.items()):
        p = memory_dir / name
        p.write_text(content)
        os.utime(p, (base_ts + i * 100, base_ts + i * 100))

    for i, (name, content) in enumerate(_DISCOVERY_FILES.items()):
        p = discoveries_dir / name
        p.write_text(content)
        os.utime(p, (base_ts + 1000 + i * 100, base_ts + 1000 + i * 100))

    for i, (name, content) in enumerate(_SESSION_FILES.items()):
        p = sessions_dir / name
        p.write_text(content)
        os.utime(p, (base_ts + 2000 + i * 100, base_ts + 2000 + i * 100))

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Local mode — no external calls, deterministic output
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_HAIKU_ENABLED", "false")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_ENABLED", "false")
    monkeypatch.delenv("DEPTHFUSION_GRAPH_ENABLED", raising=False)
    monkeypatch.delenv("DEPTHFUSION_VECTOR_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("DEPTHFUSION_FUSION_GATES_ENABLED", raising=False)
    # cross_project=True so tests don't depend on git remote detection
    # (project filter would silently drop all blocks otherwise)
    return tmp_path


# ── Schema tests ──────────────────────────────────────────────────────────


def test_explain_param_in_schema():
    """S-92: schema must declare explain as a boolean with default False."""
    schema = _make_tool_schema("depthfusion_recall_relevant", "desc")
    props = schema["inputSchema"]["properties"]
    assert "explain" in props, "explain property missing from schema"
    assert props["explain"]["type"] == "boolean"
    assert props["explain"]["default"] is False


def test_explain_not_required():
    """explain must be optional (not in required list)."""
    schema = _make_tool_schema("depthfusion_recall_relevant", "desc")
    assert "explain" not in schema["inputSchema"]["required"]


# ── Explain=False (default) tests ─────────────────────────────────────────


def test_explain_false_no_explain_key(fixture_home):
    """When explain is omitted or False, no block should have an explain key."""
    result = json.loads(_tool_recall_impl({
        "query": "backend provider architecture",
        "top_k": 3,
        "cross_project": True,
    }))
    blocks = result.get("blocks", [])
    assert len(blocks) > 0, "Expected at least one block in fixture corpus"
    for b in blocks:
        assert "explain" not in b, f"Unexpected explain key in block: {b}"


def test_explain_explicitly_false_no_explain_key(fixture_home):
    """When explain=False is passed explicitly, no explain key appears."""
    result = json.loads(_tool_recall_impl({
        "query": "protocol implementation",
        "top_k": 3,
        "cross_project": True,
        "explain": False,
    }))
    for b in result.get("blocks", []):
        assert "explain" not in b


# ── Explain=True tests ────────────────────────────────────────────────────


def test_explain_true_attaches_explain_block(fixture_home):
    """When explain=True, every returned block must have an explain key."""
    result = json.loads(_tool_recall_impl({
        "query": "backend provider architecture",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    blocks = result.get("blocks", [])
    assert len(blocks) > 0, "Expected at least one block in fixture corpus"
    for b in blocks:
        assert "explain" in b, f"Block missing explain key: {b}"


def test_explain_block_has_bm25_score(fixture_home):
    """explain block must contain bm25_score as a float."""
    result = json.loads(_tool_recall_impl({
        "query": "typed protocol backend",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    for b in result.get("blocks", []):
        ex = b["explain"]
        assert "bm25_score" in ex, f"bm25_score missing from explain: {ex}"
        assert isinstance(ex["bm25_score"], float), (
            f"bm25_score should be float, got {type(ex['bm25_score'])}"
        )


def test_explain_block_has_reranker_rank(fixture_home):
    """explain block must contain reranker_rank as a non-negative int."""
    result = json.loads(_tool_recall_impl({
        "query": "typed protocol backend",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    for rank_idx, b in enumerate(result.get("blocks", [])):
        ex = b["explain"]
        assert "reranker_rank" in ex, f"reranker_rank missing from explain: {ex}"
        assert isinstance(ex["reranker_rank"], int), (
            f"reranker_rank should be int, got {type(ex['reranker_rank'])}"
        )
        assert ex["reranker_rank"] == rank_idx, (
            f"reranker_rank {ex['reranker_rank']} != position {rank_idx}"
        )


def test_explain_block_has_rrf_score(fixture_home):
    """explain block must contain rrf_score (the final blended score)."""
    result = json.loads(_tool_recall_impl({
        "query": "pluggable backend design",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    for b in result.get("blocks", []):
        ex = b["explain"]
        assert "rrf_score" in ex, f"rrf_score missing from explain: {ex}"
        assert isinstance(ex["rrf_score"], (int, float)), (
            f"rrf_score should be numeric, got {type(ex['rrf_score'])}"
        )


def test_explain_does_not_change_score_or_snippet(fixture_home):
    """explain=True must not alter score or snippet compared to explain=False."""
    query = "backend provider typed protocol"
    without = json.loads(_tool_recall_impl({
        "query": query, "top_k": 3, "cross_project": True, "explain": False,
    }))
    with_explain = json.loads(_tool_recall_impl({
        "query": query, "top_k": 3, "cross_project": True, "explain": True,
    }))
    blocks_without = without.get("blocks", [])
    blocks_with = with_explain.get("blocks", [])
    assert len(blocks_without) == len(blocks_with), "Block count differs"
    for bw, be in zip(blocks_without, blocks_with):
        assert bw["score"] == be["score"], (
            f"score changed: {bw['score']} vs {be['score']}"
        )
        assert bw["snippet"] == be["snippet"], "snippet changed with explain=True"
        assert bw["chunk_id"] == be["chunk_id"], "chunk_id changed with explain=True"


def test_explain_block_no_sensitive_data(fixture_home, monkeypatch):
    """AC-4: explain block must not contain env var values or sensitive data.

    We set a recognisable sentinel env value; it must not appear in any
    explain block field value.
    """
    sentinel = "SUPER_SECRET_API_KEY_VALUE_XYZ"
    monkeypatch.setenv("DEPTHFUSION_API_KEY", sentinel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)

    result = json.loads(_tool_recall_impl({
        "query": "backend architecture protocol",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    for b in result.get("blocks", []):
        ex = b.get("explain", {})
        explain_str = json.dumps(ex)
        assert sentinel not in explain_str, (
            f"Sensitive env var value leaked into explain block: {explain_str}"
        )


def test_explain_block_only_numeric_and_bool_values(fixture_home):
    """AC-4: explain block values must be numeric, bool, or None only — no strings."""
    result = json.loads(_tool_recall_impl({
        "query": "typed backend provider",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    for b in result.get("blocks", []):
        ex = b.get("explain", {})
        for key, val in ex.items():
            assert isinstance(val, (int, float, bool, type(None))), (
                f"explain[{key!r}] = {val!r} is not numeric/bool/None — "
                "no strings or path components allowed"
            )


def test_explain_no_internal_explain_field_leaked(fixture_home):
    """The internal _explain field must never appear in output blocks."""
    result = json.loads(_tool_recall_impl({
        "query": "backend architecture",
        "top_k": 5,
        "cross_project": True,
        "explain": True,
    }))
    for b in result.get("blocks", []):
        assert "_explain" not in b, f"Internal _explain field leaked into output: {b}"


# ── Recency mode (empty query) ────────────────────────────────────────────


def test_explain_absent_in_recency_mode(fixture_home):
    """When query is empty, recency mode runs — explain should not appear."""
    result = json.loads(_tool_recall_impl({
        "query": "",
        "top_k": 3,
        "cross_project": True,
        "explain": True,
    }))
    for b in result.get("blocks", []):
        assert "explain" not in b, (
            f"explain key present in recency-mode block: {b}"
        )
