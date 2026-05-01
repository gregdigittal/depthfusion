"""Tests for E-27 / S-72 — recall feedback loop.

≥ 6 tests required by AC-6; 18 delivered to cover consensus-anticipated
scenarios (concurrency, lock-helper interactions, eviction edge cases).

Skip-gated on the implementation modules so the suite stays green during
the TDD red phase. Lifts automatically as Commit 2 (file_locking) and
Commit 3 (feedback) land.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Skip gates
# ---------------------------------------------------------------------------

try:
    from depthfusion.core.file_locking import atomic_frontmatter_rewrite
    LOCK_HELPER_AVAILABLE = True
except ImportError:
    LOCK_HELPER_AVAILABLE = False
    atomic_frontmatter_rewrite = None  # type: ignore[assignment]

try:
    from depthfusion.core.feedback import (
        RecallStore,
        RECALL_TTL_SECONDS,
        USED_BOOST,
        IGNORED_DECAY,
    )
    FEEDBACK_AVAILABLE = True
except ImportError:
    FEEDBACK_AVAILABLE = False
    RecallStore = None  # type: ignore[assignment]
    RECALL_TTL_SECONDS = 86400
    USED_BOOST = 0.1
    IGNORED_DECAY = 0.05

try:
    from depthfusion.mcp.server import _tool_recall_feedback
    TOOL_AVAILABLE = True
except ImportError:
    TOOL_AVAILABLE = False
    _tool_recall_feedback = None  # type: ignore[assignment]

lock_helper_required = pytest.mark.skipif(
    not LOCK_HELPER_AVAILABLE, reason="S-72 Commit 2 file_locking helper not yet present",
)
feedback_required = pytest.mark.skipif(
    not FEEDBACK_AVAILABLE, reason="S-72 Commit 3 RecallStore not yet present",
)
tool_required = pytest.mark.skipif(
    not TOOL_AVAILABLE, reason="S-72 Commit 3 _tool_recall_feedback not yet present",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_discovery(tmp_path: Path, file_stem: str, salience: float = 1.0) -> Path:
    """Write a minimal discovery file with importance/salience frontmatter."""
    f = tmp_path / f"{file_stem}.md"
    f.write_text(
        "---\n"
        f"project: testproj\n"
        "session_id: test-sess\n"
        "type: decisions\n"
        "importance: 0.5000\n"
        f"salience: {salience:.4f}\n"
        "---\n"
        "\n# Decisions\n- A test decision\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def store():
    """Fresh RecallStore per test (singleton-reset pattern)."""
    s = RecallStore()
    yield s
    # No teardown needed — fresh instance per test


# ---------------------------------------------------------------------------
# AC-1 / AC-2: recall_id minting and short-term store
# ---------------------------------------------------------------------------

@feedback_required
class TestRecallIdMinting:
    def test_register_recall_returns_uuid4_format(self, store):
        """AC-1: recall_id is a uuid4-formatted string."""
        import re
        rid = store.register_recall(["c1", "c2"])
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            rid,
        ), f"not uuid4: {rid}"

    def test_recall_id_is_unique_per_call(self, store):
        """AC-1: N calls produce N distinct ids."""
        ids = {store.register_recall(["c1"]) for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# AC-3 / AC-5: bumps + bounds
# ---------------------------------------------------------------------------

@feedback_required
class TestFeedbackBumps:
    def test_used_boost_applies(self, store, tmp_path, monkeypatch):
        """AC-3: +0.1 per used chunk to discovery file salience."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        f = _seed_discovery(tmp_path, "2026-05-01-testproj-decisions", salience=1.0)
        rid = store.register_recall(["2026-05-01-testproj-decisions"])
        result = store.apply_feedback(
            rid, used=["2026-05-01-testproj-decisions"], ignored=[],
        )
        assert result.applied == 1
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(f.read_text(encoding="utf-8"))
        assert score.salience == pytest.approx(1.1, abs=1e-3)

    def test_ignored_decay_applies(self, store, tmp_path, monkeypatch):
        """AC-3: -0.05 per ignored chunk."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        f = _seed_discovery(tmp_path, "2026-05-01-testproj-decisions", salience=2.0)
        rid = store.register_recall(["2026-05-01-testproj-decisions"])
        result = store.apply_feedback(
            rid, used=[], ignored=["2026-05-01-testproj-decisions"],
        )
        assert result.applied == 1
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(f.read_text(encoding="utf-8"))
        assert score.salience == pytest.approx(1.95, abs=1e-3)

    def test_clamps_at_max_5(self, store, tmp_path, monkeypatch):
        """AC-5: pushing past 5.0 clamps to 5.0."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        f = _seed_discovery(tmp_path, "f1", salience=4.95)
        rid = store.register_recall(["f1"])
        # Two used chunks would push to 5.15; clamp to 5.0.
        store.apply_feedback(rid, used=["f1", "f1"], ignored=[])
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(f.read_text(encoding="utf-8"))
        assert score.salience == 5.0

    def test_clamps_at_min_0(self, store, tmp_path, monkeypatch):
        """AC-5: pushing below 0.0 clamps to 0.0."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        f = _seed_discovery(tmp_path, "f1", salience=0.02)
        rid = store.register_recall(["f1"])
        # Ignored decay would push to -0.03; clamp to 0.0.
        store.apply_feedback(rid, used=[], ignored=["f1"])
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(f.read_text(encoding="utf-8"))
        assert score.salience == 0.0

    def test_batches_multiple_chunks_per_file(self, store, tmp_path, monkeypatch):
        """3 used chunks pointing to same file → single RMW with delta=+0.3."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        f = _seed_discovery(tmp_path, "f1", salience=1.0)
        rid = store.register_recall(["f1#0", "f1#1", "f1#2"])
        # All three chunk_ids map to file_stem "f1".
        result = store.apply_feedback(
            rid, used=["f1#0", "f1#1", "f1#2"], ignored=[],
        )
        assert result.applied == 3
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(f.read_text(encoding="utf-8"))
        assert score.salience == pytest.approx(1.3, abs=1e-3)


# ---------------------------------------------------------------------------
# AC-4: idempotency
# ---------------------------------------------------------------------------

@feedback_required
class TestIdempotency:
    def test_replay_same_payload_skips_applied(self, store, tmp_path, monkeypatch):
        """AC-4: same payload twice; second call lands all in skipped_already_applied."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        _seed_discovery(tmp_path, "f1", salience=1.0)
        rid = store.register_recall(["f1"])
        first = store.apply_feedback(rid, used=["f1"], ignored=[])
        second = store.apply_feedback(rid, used=["f1"], ignored=[])
        assert first.applied == 1
        assert second.applied == 0
        assert second.skipped_already_applied == 1

    def test_partial_replay_lands_only_new_chunks(self, store, tmp_path, monkeypatch):
        """First call applies 2/3; retry with all 3 applies only the missing 1."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        _seed_discovery(tmp_path, "f1", salience=1.0)
        _seed_discovery(tmp_path, "f2", salience=1.0)
        _seed_discovery(tmp_path, "f3", salience=1.0)
        rid = store.register_recall(["f1", "f2", "f3"])
        store.apply_feedback(rid, used=["f1", "f2"], ignored=[])
        retry = store.apply_feedback(rid, used=["f1", "f2", "f3"], ignored=[])
        assert retry.applied == 1
        assert retry.skipped_already_applied == 2


# ---------------------------------------------------------------------------
# Eviction / expiry
# ---------------------------------------------------------------------------

@feedback_required
class TestEvictionAndExpiry:
    def test_expired_recall_id_skips_all(self, store, tmp_path, monkeypatch):
        """AC-6: clock advances past TTL; feedback returns skipped_expired."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        _seed_discovery(tmp_path, "f1")
        with patch("depthfusion.core.feedback.time.time", return_value=1000.0):
            rid = store.register_recall(["f1"])
        with patch(
            "depthfusion.core.feedback.time.time",
            return_value=1000.0 + RECALL_TTL_SECONDS + 1,
        ):
            result = store.apply_feedback(rid, used=["f1"], ignored=[])
        assert result.applied == 0
        assert result.skipped_expired == 1

    def test_unknown_recall_id_skips_all(self, store, tmp_path):
        """A uuid that was never minted → skipped_missing (distinct from expired)."""
        result = store.apply_feedback(
            "00000000-0000-4000-8000-000000000000", used=["f1"], ignored=[],
        )
        assert result.skipped_missing == 1
        assert result.skipped_expired == 0

    def test_sweep_on_write_evicts_stale(self, store, tmp_path):
        """Register many entries, advance clock, register one more; old entries gone."""
        with patch("depthfusion.core.feedback.time.time", return_value=1000.0):
            stale_ids = [store.register_recall(["fx"]) for _ in range(5)]
        with patch(
            "depthfusion.core.feedback.time.time",
            return_value=1000.0 + RECALL_TTL_SECONDS + 1,
        ):
            store.register_recall(["fy"])
        # All stale entries should be evicted by the sweep — meaning
        # apply_feedback for them returns skipped_missing (not
        # skipped_expired, which would indicate the entry was still in
        # the dict but TTL-checked at apply time without a sweep). This
        # tightening is consensus-driven (Round 1, Commit 1): the looser
        # `expired + missing == 1` form would silently bless an
        # implementation with no actual sweep.
        for sid in stale_ids:
            result = store.apply_feedback(sid, used=["fx"], ignored=[])
            assert result.skipped_missing == 1, (
                f"stale id {sid} not swept (still in dict?): {result}"
            )
            assert result.skipped_expired == 0, (
                f"sweep did not run before TTL check for {sid}: {result}"
            )


# ---------------------------------------------------------------------------
# Source / file resolution edges
# ---------------------------------------------------------------------------

@feedback_required
class TestChunkResolution:
    def test_unresolvable_file_skipped_missing(self, store, tmp_path, monkeypatch):
        """A registered chunk_id whose file can't be resolved → skipped_missing.

        Consensus Round 1 fix: the previous test name claimed to test
        ``skipped_unsupported`` but the assertion checked ``skipped_missing``.
        This test now matches its name. ``skipped_unsupported`` gets its
        own dedicated test (``test_unregistered_chunk_skipped_unsupported``)
        that exercises the actual unsupported-source path.
        """
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        rid = store.register_recall(["nonexistent-stem"])
        result = store.apply_feedback(rid, used=["nonexistent-stem"], ignored=[])
        assert result.skipped_missing == 1
        assert result.skipped_unsupported == 0

    def test_unregistered_chunk_skipped_unsupported(self, store, tmp_path, monkeypatch):
        """A chunk_id that was never part of the recall → skipped_unsupported.

        Consensus-driven (Round 1, Commit 1): without this test, the
        ``skipped_unsupported`` bucket of the FeedbackResult shape has
        zero assertion coverage. ``_bucket_chunk`` returns ``"unsupported"``
        when ``chunk_id not in registered`` (caller sent a chunk that
        wasn't part of this recall). This test locks that path in.
        """
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        _seed_discovery(tmp_path, "registered-stem")
        rid = store.register_recall(["registered-stem"])
        # Send feedback for a chunk_id that was NOT in the original recall.
        result = store.apply_feedback(rid, used=["unregistered-stem"], ignored=[])
        assert result.skipped_unsupported == 1
        assert result.skipped_missing == 0

    def test_archived_file_skipped_missing(self, store, tmp_path, monkeypatch):
        """File exists in .archive/ but not main dir → skipped_missing."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        archive_dir = tmp_path / ".archive"
        archive_dir.mkdir()
        (archive_dir / "f1.md").write_text("---\nsalience: 1.0\n---\n", encoding="utf-8")
        rid = store.register_recall(["f1"])
        result = store.apply_feedback(rid, used=["f1"], ignored=[])
        assert result.skipped_missing == 1

    def test_superseded_file_skipped_missing(self, store, tmp_path, monkeypatch):
        """A .superseded file in the main dir → skipped_missing (no .md)."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        (tmp_path / "f1.md.superseded").write_text(
            "---\nsalience: 1.0\n---\n", encoding="utf-8",
        )
        rid = store.register_recall(["f1"])
        result = store.apply_feedback(rid, used=["f1"], ignored=[])
        assert result.skipped_missing == 1


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

@feedback_required
class TestConcurrency:
    def test_concurrent_feedback_different_recall_ids(self, store, tmp_path, monkeypatch):
        """Two threads, two recall_ids, two different files: both apply cleanly.

        Consensus-driven assertion (Round 1, Commit 1): verify the actual
        persisted file contents, not just the in-memory result counters —
        an implementation that increments `applied` but fails to write
        the file would otherwise silently pass.
        """
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        fA = _seed_discovery(tmp_path, "fA", salience=1.0)
        fB = _seed_discovery(tmp_path, "fB", salience=1.0)
        rA = store.register_recall(["fA"])
        rB = store.register_recall(["fB"])

        results = []
        lock = threading.Lock()

        def _run(rid, chunk):
            r = store.apply_feedback(rid, used=[chunk], ignored=[])
            with lock:
                results.append(r)

        threads = [
            threading.Thread(target=_run, args=(rA, "fA")),
            threading.Thread(target=_run, args=(rB, "fB")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(results) == 2
        assert all(r.applied == 1 for r in results)

        # Verify both files actually got their salience bumped on disk.
        from depthfusion.capture.dedup import extract_memory_score
        scoreA = extract_memory_score(fA.read_text(encoding="utf-8"))
        scoreB = extract_memory_score(fB.read_text(encoding="utf-8"))
        assert scoreA.salience == pytest.approx(1.1, abs=1e-3), (
            f"fA bump did not persist: {scoreA.salience}"
        )
        assert scoreB.salience == pytest.approx(1.1, abs=1e-3), (
            f"fB bump did not persist: {scoreB.salience}"
        )

    @tool_required
    def test_concurrent_partial_updates_serialize(self, store, tmp_path, monkeypatch):
        """set_memory_score and recall_feedback racing on same file: both updates land."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        f = _seed_discovery(tmp_path, "fX", salience=1.0)
        rid = store.register_recall(["fX"])

        from depthfusion.mcp.server import _tool_set_memory_score

        results = []
        lock = threading.Lock()

        def _via_feedback():
            r = store.apply_feedback(rid, used=["fX"], ignored=[])
            with lock:
                results.append(("fb", r))

        def _via_set_memory():
            r = _tool_set_memory_score({"filename": str(f), "importance": 0.92})
            with lock:
                results.append(("set", json.loads(r)))

        threads = [threading.Thread(target=_via_feedback), threading.Thread(target=_via_set_memory)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        # Both operations should report success.
        assert len(results) == 2
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(f.read_text(encoding="utf-8"))
        # set_memory_score landed importance=0.92; feedback landed salience=+0.1.
        assert score.importance == pytest.approx(0.92, abs=1e-3)
        assert score.salience == pytest.approx(1.1, abs=1e-3)


# ---------------------------------------------------------------------------
# MCP tool surface (uses real _tool_recall_feedback)
# ---------------------------------------------------------------------------

@tool_required
class TestMCPTool:
    def test_malformed_payload_returns_structured_error(self):
        """Non-string recall_id, non-list used → {ok: false, error}."""
        result = json.loads(_tool_recall_feedback({"recall_id": 123, "used": [], "ignored": []}))
        assert result.get("ok") is False
        assert "error" in result

        result2 = json.loads(_tool_recall_feedback(
            {"recall_id": "abc", "used": "not-a-list", "ignored": []},
        ))
        assert result2.get("ok") is False
        assert "error" in result2

    def test_response_shape_has_all_buckets(self, tmp_path, monkeypatch):
        """Successful call returns the documented bucket-counts shape.

        Consensus Round 1 fix: use the spec'd ``RecallStore.reset_singleton()``
        helper rather than poking at a private ``_RECALL_STORE_INSTANCE``
        attribute on the server module — the latter is implementation
        detail not in the spec, and a Phase 3 rename would silently
        no-op the reset and let stale state leak into this test.
        """
        from depthfusion.core import feedback as fb_module

        monkeypatch.setattr(fb_module, "_discoveries_dir", lambda: tmp_path)
        # Fresh singleton via the spec'd reset helper.
        fb_module.RecallStore.reset_singleton()
        _seed_discovery(tmp_path, "f1")
        rid = fb_module.RecallStore.singleton().register_recall(["f1"])

        result = json.loads(_tool_recall_feedback(
            {"recall_id": rid, "used": ["f1"], "ignored": []},
        ))
        assert result["ok"] is True
        for key in (
            "applied", "skipped_unsupported", "skipped_missing",
            "skipped_already_applied", "skipped_expired",
        ):
            assert key in result, f"missing bucket: {key}"


# ---------------------------------------------------------------------------
# Lock helper unit tests (Commit 2 lifts these)
# ---------------------------------------------------------------------------

@lock_helper_required
class TestLockHelper:
    def test_atomic_rewrite_preserves_other_frontmatter(self, tmp_path):
        """Helper updates importance/salience without disturbing other fields."""
        f = _seed_discovery(tmp_path, "f1", salience=1.0)
        with atomic_frontmatter_rewrite(f) as ctx:
            ctx.set_score(importance=0.7, salience=2.0)
        body = f.read_text(encoding="utf-8")
        assert "project: testproj" in body
        assert "type: decisions" in body
        from depthfusion.capture.dedup import extract_memory_score
        score = extract_memory_score(body)
        assert score.importance == pytest.approx(0.7, abs=1e-3)
        assert score.salience == pytest.approx(2.0, abs=1e-3)
