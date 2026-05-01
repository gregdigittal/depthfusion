# tests/test_regression/test_v04_output_identity.py
"""Byte-identical regression test for _tool_recall output (v0.4.x baseline).

Captures the current output of `depthfusion.mcp.server._tool_recall` against
a fixed fixture corpus as a golden JSON. Any change to the retrieval
pipeline that produces different output for the same input breaks this test.

LOAD-BEARING for T-120 migration (TG-01 AC-01-3): every call-site flip from
direct `anthropic.Anthropic(...)` to `get_backend(...).rerank(...)` MUST
preserve byte-identical output in local mode (where no LLM reranker runs).
This file is the gate; it must pass before any call-site migration lands,
and must continue to pass after.

Backlog: T-121 (AC-01-3).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ── Fixture corpus ───────────────────────────────────────────────────────

# Three memory files, one discovery, one session — enough to exercise
# source-type weighting (memory 1.0, discovery 0.85, session 0.70) and
# the dedup-by-file-stem logic. Content is deliberately verbose so BM25
# has terms to differentiate.

_MEMORY_FILES = {
    "preferences.md": (
        "# Preferences\n\n"
        "## Coding style\n"
        "Use tabs for indentation. Prefer const over let. No any types in strict mode.\n\n"
        "## Conventions\n"
        "Follow the existing naming conventions. PascalCase for classes, "
        "camelCase for functions. Document public APIs.\n"
    ),
    "architecture.md": (
        "# Architecture\n\n"
        "## Backend provider interface\n"
        "The backend provider abstracts LLM backends behind a protocol. "
        "Pluggable implementations for Haiku, Gemma, and Null.\n\n"
        "## Design principles\n"
        "Pluggable. Typed errors. Graceful degradation. No vendor lock-in.\n"
    ),
    "project-alpha.md": (
        "# Project Alpha\n\n"
        "## Status\n"
        "Active. Widget deadline 2026-05-01. Team-lead owns architecture decisions.\n\n"
        "## Stakeholders\n"
        "Three contributors. Weekly sync meetings. Shared Notion workspace.\n"
    ),
}

_DISCOVERY_FILES = {
    "2026-04-10-backend-design.md": (
        "# Backend Design Discovery\n\n"
        "## Context\n"
        "Designing the backend provider interface for v0.5. "
        "Provider pattern with typed protocol.\n\n"
        "## Decision\n"
        "Use a Protocol class with runtime_checkable. "
        "Four typed error classes for fallback-chain dispatch.\n"
    ),
}

_SESSION_FILES = {
    "2026-04-15-test-session.tmp": (
        "# Session transcript\n\n"
        "## Work\n"
        "Implemented the protocol. Tests pass. Backend factory dispatches correctly.\n"
    ),
}


@pytest.fixture
def fixture_home(tmp_path, monkeypatch):
    """Build the three source directories under tmp_path with deterministic
    mtimes and content. Monkeypatch `Path.home()` so `_tool_recall` uses it.
    """
    # Directory structure matching _tool_recall's expectations
    memory_dir = tmp_path / ".claude" / "projects" / "-home-gregmorris" / "memory"
    discoveries_dir = tmp_path / ".claude" / "shared" / "discoveries"
    sessions_dir = tmp_path / ".claude" / "sessions"
    for d in (memory_dir, discoveries_dir, sessions_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Write files with explicitly set mtimes so sort-order is deterministic.
    # Earlier timestamps = older files. Spacing of 100s keeps ordering stable.
    base_ts = 1700000000.0  # 2023-11-14

    for i, (name, content) in enumerate(_MEMORY_FILES.items()):
        p = memory_dir / name
        p.write_text(content)
        ts = base_ts + i * 100
        os.utime(p, (ts, ts))

    for i, (name, content) in enumerate(_DISCOVERY_FILES.items()):
        p = discoveries_dir / name
        p.write_text(content)
        ts = base_ts + 1000 + i * 100
        os.utime(p, (ts, ts))

    for i, (name, content) in enumerate(_SESSION_FILES.items()):
        p = sessions_dir / name
        p.write_text(content)
        ts = base_ts + 2000 + i * 100
        os.utime(p, (ts, ts))

    # Monkeypatch Path.home to return our fixture root
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Local mode: no Haiku reranker, no external calls.
    # Ensures output is 100% BM25+source-weight+recency-determined.
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_HAIKU_ENABLED", "false")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_ENABLED", "false")

    return tmp_path


# ── The regression tests ─────────────────────────────────────────────────


def _strip_recall_id(output: dict) -> dict:
    """Remove recall_id before golden comparisons (S-72: uuid changes each call)."""
    return {k: v for k, v in output.items() if k != "recall_id"}


def test_recall_output_matches_v04_baseline(fixture_home):
    """Primary regression guard: _tool_recall output on fixture corpus
    must match the captured v0.4.x golden file byte-for-byte.

    On first run (no golden file), captures the current output as the
    baseline and skips. Subsequent runs assert equality.

    If an intentional pipeline change produces different output, delete
    the golden file and re-run to re-capture — but note the change
    prominently in the commit message (this is the v0.5 / T-120 gate).

    Note: recall_id (S-72) is stripped before comparison — it is a uuid4
    that changes each call and is not part of the deterministic pipeline output.
    """
    from depthfusion.mcp.server import _tool_recall

    output_json = _tool_recall({
        "query": "backend provider architecture",
        "top_k": 3,
        "snippet_len": 500,
    })
    output = _strip_recall_id(json.loads(output_json))

    golden_path = Path(__file__).parent / "golden" / "v04_recall_output.json"
    if not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(output, indent=2) + "\n")
        pytest.skip(
            f"Captured baseline to {golden_path}. "
            "Re-run the test to assert stability."
        )

    golden = _strip_recall_id(json.loads(golden_path.read_text()))
    assert output == golden, (
        f"Recall output drifted from v0.4.x baseline.\n"
        f"If this change is intentional, delete {golden_path} and "
        f"re-run to capture the new baseline, and note the change "
        f"in the commit message (this is the T-120 migration gate)."
    )


def test_recall_output_is_deterministic_across_runs(fixture_home):
    """Two consecutive calls on the same fixture must produce identical output.
    This catches non-determinism (e.g., unstable sort, random tie-break).

    Note: recall_id (S-72) is stripped before comparison — it is a uuid4
    that changes each call intentionally and does not indicate non-determinism
    in the retrieval pipeline.
    """
    from depthfusion.mcp.server import _tool_recall
    args = {"query": "architecture decisions", "top_k": 3, "snippet_len": 500}
    first = _strip_recall_id(json.loads(_tool_recall(args)))
    second = _strip_recall_id(json.loads(_tool_recall(args)))
    assert first == second


def test_recall_empty_query_returns_recency_ordered_with_half_scores(fixture_home):
    """Empty query exercises the no-query branch (returns recency order
    with uniform score=0.5). Covers a distinct code path from the BM25 branch.
    """
    from depthfusion.mcp.server import _tool_recall
    output = json.loads(_tool_recall({"query": "", "top_k": 5}))
    assert all(b["score"] == 0.5 for b in output["blocks"])
    # Score shape: every block has exactly these keys
    if output["blocks"]:
        required = {"chunk_id", "source", "score", "snippet"}
        assert set(output["blocks"][0].keys()) == required


def test_recall_respects_top_k_boundary(fixture_home):
    """top_k must bound the returned block count.

    This is a non-trivial property: the BM25 ranking runs over all blocks,
    then the reranker / dedup step may reduce count via file-stem dedup,
    and finally top_k caps the final result.
    """
    from depthfusion.mcp.server import _tool_recall
    output = json.loads(_tool_recall({
        "query": "architecture provider backend",
        "top_k": 2,
    }))
    assert len(output["blocks"]) <= 2


def test_recall_with_no_corpus_returns_empty(tmp_path, monkeypatch):
    """Graceful-degradation contract: with no ~/.claude/ source dirs,
    _tool_recall returns an empty-blocks JSON rather than raising.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    from depthfusion.mcp.server import _tool_recall
    output = json.loads(_tool_recall({"query": "anything", "top_k": 5}))
    assert output["blocks"] == []
    assert "No session context" in output.get("message", "")
