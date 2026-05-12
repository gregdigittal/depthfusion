import logging
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


def test_graph_extractor_populates_store(tmp_path, monkeypatch):
    """Graph entities extracted from session file and stored when graph_enabled=True."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    session_file = tmp_path / "session.tmp"
    session_file.write_text("The TierManager class is central.\nrrf_fuse() merges results.", encoding="utf-8")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(session_file, project="depthfusion", graph_store=store)

    entities = store.all_entities()
    names = [e.name for e in entities]
    assert "TierManager" in names


def test_graph_extraction_skipped_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "false")
    session_file = tmp_path / "session.tmp"
    session_file.write_text("TierManager is central.", encoding="utf-8")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(session_file, project="depthfusion", graph_store=store)

    assert store.node_count() == 0


# ---------------------------------------------------------------------------
# Phase 4: TemporalSessionLinker wiring (S-50 follow-up)
# ---------------------------------------------------------------------------

def _make_session_file(sessions_dir, name: str, content: str, mtime_offset_s: float = 0):
    """Create a .tmp session file with a specific mtime relative to now."""
    import os
    import time
    path = sessions_dir / name
    path.write_text(content, encoding="utf-8")
    if mtime_offset_s != 0:
        ts = time.time() + mtime_offset_s
        os.utime(path, (ts, ts))
    return path


def test_temporal_session_linker_wires_preceded_by_edges(tmp_path, monkeypatch):
    """Two sessions close in time + sharing vocabulary → PRECEDED_BY edge upserted."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    # Shared vocabulary of >=5 tokens on both sessions ensures the linker's
    # default min_overlap=5 gate passes.
    shared = (
        "authentication pipeline validator serializer handler context "
        "middleware orchestrator "
    )
    _make_session_file(
        sessions_dir, "alpha.tmp", shared + "alpha-unique-token",
        mtime_offset_s=-3600 * 2,  # 2 hours ago
    )
    current = _make_session_file(
        sessions_dir, "beta.tmp", shared + "beta-unique-token",
        mtime_offset_s=0,  # now
    )

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(current, project="testproj", graph_store=store)

    # At least one PRECEDED_BY edge should have been created
    session_entities = [e for e in store.all_entities() if e.type == "session"]
    assert len(session_entities) >= 2

    # Find the PRECEDED_BY edge — traverse from the newer session
    from depthfusion.graph.extractor import make_entity_id
    beta_id = make_entity_id("beta", "session", "testproj")
    edges = store.get_edges(beta_id, relationship_filter=["PRECEDED_BY"])
    assert len(edges) >= 1


def test_temporal_linker_disabled_by_env_flag(tmp_path, monkeypatch):
    """Setting DEPTHFUSION_TEMPORAL_SESSION_LINKER_ENABLED=false skips Phase 4."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_TEMPORAL_SESSION_LINKER_ENABLED", "false")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    shared = "alpha beta gamma delta epsilon zeta "
    _make_session_file(sessions_dir, "a.tmp", shared + "unique-a", mtime_offset_s=-3600)
    current = _make_session_file(sessions_dir, "b.tmp", shared + "unique-b")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(current, project="testproj", graph_store=store)

    # Entity-level extraction may still run; but NO session-type entities
    # should have been upserted since Phase 4 was disabled.
    session_entities = [e for e in store.all_entities() if e.type == "session"]
    assert session_entities == []


def test_temporal_linker_noop_on_single_session(tmp_path, monkeypatch):
    """With only one session file, no pair can form; Phase 4 is a clean no-op."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    current = _make_session_file(sessions_dir, "solo.tmp", "just some content here")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(current, project="testproj", graph_store=store)

    session_entities = [e for e in store.all_entities() if e.type == "session"]
    assert session_entities == []  # no pairs → no edges → no session entities


def test_temporal_linker_excludes_sessions_outside_lookback(tmp_path, monkeypatch):
    """A session file older than 72h is not considered for linking."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    shared = "authentication pipeline validator serializer handler context "
    # 100 hours ago — outside the default 72h lookback
    _make_session_file(sessions_dir, "ancient.tmp", shared + "ancient", mtime_offset_s=-3600 * 100)
    current = _make_session_file(sessions_dir, "current.tmp", shared + "current")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(current, project="testproj", graph_store=store)

    # Ancient session is outside lookback → never loaded → no pair formed
    session_entities = [e for e in store.all_entities() if e.type == "session"]
    assert session_entities == []


def test_temporal_linker_isolated_sessions_not_upserted(tmp_path, monkeypatch):
    """Sessions that don't participate in ANY edge aren't added to the graph —
    avoids bulking with unreferenced session nodes.
    """
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    # Two sessions with no vocabulary overlap → no edge qualifies.
    _make_session_file(
        sessions_dir, "cats.tmp",
        "feline whiskers purring mice-catching grooming napping sunbeams",
        mtime_offset_s=-3600,
    )
    current = _make_session_file(
        sessions_dir, "rockets.tmp",
        "thrust propellant nozzle trajectory orbit payload booster",
    )

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(current, project="testproj", graph_store=store)

    session_entities = [e for e in store.all_entities() if e.type == "session"]
    assert session_entities == []


# ---------------------------------------------------------------------------
# T-326: ContradictionEngine feature flag wiring
# ---------------------------------------------------------------------------

def test_contradiction_engine_disabled_by_default(tmp_path, monkeypatch, caplog):
    """DEPTHFUSION_CONTRADICTION_ENGINE defaults off — no [contradiction] log lines."""
    monkeypatch.delenv("DEPTHFUSION_CONTRADICTION_ENGINE", raising=False)

    session_file = tmp_path / "sess.tmp"
    # Two decisions that would trigger negation detection if the engine ran.
    session_file.write_text(
        "→ Always use RS256 for JWT signing\n"
        "→ Never use RS256 for JWT signing\n",
        encoding="utf-8",
    )

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    with caplog.at_level(logging.WARNING, logger="depthfusion.capture.auto_learn"):
        summarize_and_extract_graph(session_file, project="testproj", graph_store=None)

    contradiction_warnings = [r for r in caplog.records if "[contradiction]" in r.message]
    assert contradiction_warnings == [], (
        "Expected no contradiction warnings when engine flag is off"
    )


def test_contradiction_engine_enabled_no_conflicts(tmp_path, monkeypatch, caplog):
    """Engine enabled but decisions don't contradict → no [contradiction] warnings."""
    monkeypatch.setenv("DEPTHFUSION_CONTRADICTION_ENGINE", "true")

    session_file = tmp_path / "sess.tmp"
    # Two distinct non-conflicting decisions.
    session_file.write_text(
        "→ Use PostgreSQL for persistent storage\n"
        "→ Use Redis for session caching\n",
        encoding="utf-8",
    )

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    with caplog.at_level(logging.WARNING, logger="depthfusion.capture.auto_learn"):
        summarize_and_extract_graph(session_file, project="testproj", graph_store=None)

    contradiction_warnings = [r for r in caplog.records if "[contradiction]" in r.message]
    assert contradiction_warnings == [], (
        "Expected no contradiction warnings for non-conflicting decisions"
    )


def test_contradiction_engine_enabled_high_severity_logged(tmp_path, monkeypatch, caplog):
    """Engine enabled + HIGH severity conflict → WARNING log with [contradiction] prefix."""
    monkeypatch.setenv("DEPTHFUSION_CONTRADICTION_ENGINE", "true")

    session_file = tmp_path / "sess.tmp"
    # Negation pair with high confidence (score=0.75 by default, min=0.75 → HIGH).
    # ContradictionEngine raises HIGH when min_confidence > 0.8; with our
    # default of 0.75 the severity is MEDIUM — but we can test the log format
    # using a direct call to _run_contradiction_detection with a crafted
    # decision list that we know will produce a high-severity conflict.
    # The simplest approach: write content that produces the negation pair,
    # then verify the [contradiction] prefix appears at WARNING level by
    # patching ContradictionEngine to return a HIGH conflict directly.
    session_file.write_text("→ do not use HS256 ever\n", encoding="utf-8")

    from depthfusion.cognitive.contradiction import (
        Conflict,
        ConflictSeverity,
        ConflictStatus,
    )

    # Patch ContradictionEngine.detect to return a HIGH conflict unconditionally.
    import unittest.mock as mock
    high_conflict = Conflict(
        memory_a_id="a",
        memory_b_id="b",
        conflict_type="negation",
        description="Test HIGH conflict",
        severity=ConflictSeverity.HIGH,
        status=ConflictStatus.AUTO_EMITTED,
        confidence_a=0.9,
        confidence_b=0.9,
    )

    with mock.patch(
        "depthfusion.cognitive.contradiction.ContradictionEngine.detect",
        return_value=[high_conflict],
    ):
        from depthfusion.capture.auto_learn import _run_contradiction_detection

        with caplog.at_level(logging.WARNING, logger="depthfusion.capture.auto_learn"):
            _run_contradiction_detection(
                ["always use RS256", "never use RS256"],
                session_id="test-session",
                project="testproj",
            )

    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "[contradiction]" in r.message
    ]
    assert len(warning_records) >= 1, "Expected at least one WARNING with [contradiction] prefix"
    assert "negation" in warning_records[0].message
    assert "severity=high" in warning_records[0].message


def test_contradiction_engine_import_failure_does_not_crash(tmp_path, monkeypatch, caplog):
    """If ContradictionEngine import fails, extraction continues without raising."""
    monkeypatch.setenv("DEPTHFUSION_CONTRADICTION_ENGINE", "true")

    session_file = tmp_path / "sess.tmp"
    session_file.write_text(
        "→ always use RS256\n→ never use RS256\n",
        encoding="utf-8",
    )

    import sys
    import unittest.mock as mock

    # Simulate ImportError for the contradiction module.
    with mock.patch.dict(
        sys.modules,
        {"depthfusion.cognitive.contradiction": None},  # type: ignore[dict-item]
    ):
        from depthfusion.capture.auto_learn import _run_contradiction_detection
        # Should not raise.
        _run_contradiction_detection(
            ["always use RS256", "never use RS256"],
            session_id="test-session",
            project="testproj",
        )


def test_contradiction_engine_does_not_block_extraction(tmp_path, monkeypatch):
    """Even if contradiction detection raises internally, extraction completes."""
    monkeypatch.setenv("DEPTHFUSION_CONTRADICTION_ENGINE", "true")

    session_file = tmp_path / "sess.tmp"
    session_file.write_text(
        "→ always use RS256\n→ never use RS256\n",
        encoding="utf-8",
    )

    import unittest.mock as mock

    # Make ContradictionEngine.detect blow up.
    with mock.patch(
        "depthfusion.cognitive.contradiction.ContradictionEngine.detect",
        side_effect=RuntimeError("simulated engine crash"),
    ):
        from depthfusion.capture.auto_learn import summarize_and_extract_graph
        # Must not raise.
        summarize_and_extract_graph(session_file, project="testproj", graph_store=None)
