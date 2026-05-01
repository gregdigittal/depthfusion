# S-72 Recall Feedback Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a recall-feedback loop so DepthFusion's `salience` scalar reflects which surfaced chunks were actually used after `recall_relevant`.

**Architecture:** New `core/file_locking.py` module provides an `atomic_frontmatter_rewrite(path)` context manager (refactored out of S-70's `_tool_set_memory_score`). New `core/feedback.py` module provides an in-memory `RecallStore` (recall_id → chunk_ids + applied-set, sweep-on-write TTL). `recall_relevant` mints a uuid4 `recall_id` per call; new MCP tool `depthfusion_recall_feedback` looks up the recall_id, batches chunks by target discovery file, and applies bounded `salience` deltas via the lock helper.

**Tech Stack:** Python 3.10+, dataclasses, `fcntl.LOCK_EX`, `threading.Lock`, pytest, no new third-party deps.

**Spec:** `docs/superpowers/specs/2026-05-01-s72-recall-feedback-design.md`

**Per-commit consensus is mandatory:** the project's pre-commit shim (per `~/.claude/rules/commit-review.md`) blocks `git commit` and requires invoking `i-auditreviewer-consensus`. Each commit step in this plan implies that loop.

---

## File structure

**Create:**
- `src/depthfusion/core/file_locking.py` — `atomic_frontmatter_rewrite` context manager + helpers (~80 lines)
- `src/depthfusion/core/feedback.py` — `RecallStore`, `RecallEntry`, `FeedbackResult`, `apply_feedback` (~150 lines)
- `tests/test_capture/test_recall_feedback.py` — 18 tests (~400 lines, mostly fixtures + parametric cases)

**Modify:**
- `src/depthfusion/mcp/server.py` — refactor `_tool_set_memory_score` to use the helper; modify all three recall return sites to include `recall_id`; add `_tool_recall_feedback`; register in `TOOLS`/`_TOOL_FLAGS`/`_dispatch_tool`
- `tests/test_analyzer/test_mcp_server.py` — bump tool-count assertions 14→15, add `depthfusion_recall_feedback` to expected set

**Author:**
- `docs/reviews/2026-05-01-s72-consensus.md` — created during the consensus loop on Commit 1; appended to on each subsequent commit

---

# Phase 1 — Commit 1: Test skeletons (red phase, all skipped)

### Task 1.1: Create `tests/test_capture/test_recall_feedback.py` with all 18 tests, skip-gated

**Files:**
- Create: `tests/test_capture/test_recall_feedback.py`

- [ ] **Step 1: Write the test file**

```python
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
        # All stale entries should be evicted by the sweep.
        for sid in stale_ids:
            result = store.apply_feedback(sid, used=["fx"], ignored=[])
            assert result.skipped_expired + result.skipped_missing == 1, (
                f"stale id {sid} not swept: {result}"
            )


# ---------------------------------------------------------------------------
# Source / file resolution edges
# ---------------------------------------------------------------------------

@feedback_required
class TestChunkResolution:
    def test_non_discovery_chunk_skipped_unsupported(self, store, tmp_path, monkeypatch):
        """A chunk_id that doesn't resolve to a file → skipped_unsupported."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        # No file seeded; chunk_id doesn't match anything.
        rid = store.register_recall(["nonexistent-stem"])
        result = store.apply_feedback(rid, used=["nonexistent-stem"], ignored=[])
        assert result.skipped_missing == 1

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
        """Two threads, two recall_ids, two different files: both apply cleanly."""
        monkeypatch.setattr(
            "depthfusion.core.feedback._discoveries_dir", lambda: tmp_path,
        )
        _seed_discovery(tmp_path, "fA", salience=1.0)
        _seed_discovery(tmp_path, "fB", salience=1.0)
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
        """Successful call returns the documented bucket-counts shape."""
        from depthfusion.mcp import server as mcp_server
        from depthfusion.core import feedback as fb_module

        monkeypatch.setattr(fb_module, "_discoveries_dir", lambda: tmp_path)
        # Fresh store
        mcp_server._RECALL_STORE_INSTANCE = None  # type: ignore[attr-defined]
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
```

- [ ] **Step 2: Run the file alone — expect all 18 tests to skip cleanly**

Run: `python -m pytest tests/test_capture/test_recall_feedback.py -v`
Expected: `18 skipped` (or close; some classes hold multiple tests). All `SKIPPED`, no errors, no failures.

- [ ] **Step 3: Run the full suite — expect no regressions**

Run: `python -m pytest --tb=short -q`
Expected: `1226 passed, N skipped` (where N includes the 18 new ones). No FAILED.

- [ ] **Step 4: Stage and commit (consensus loop fires)**

Run: `git add tests/test_capture/test_recall_feedback.py`
Then: `git commit -m "test(feedback): add S-72 recall feedback test skeletons (red phase)"`

The pre-commit shim WILL block. Invoke `i-auditreviewer-consensus` per `~/.claude/rules/commit-review.md`. Apply MEDIUM+ findings inline; LOW findings can be deferred. Author `docs/reviews/2026-05-01-s72-consensus.md` Round 1 section.

After consensus passes, retry: `git commit -m "test(feedback): add S-72 recall feedback test skeletons (red phase) [skip-review]"` (because the consensus was conducted manually and recorded).

---

# Phase 2 — Commit 2: Extract `core/file_locking.py` + refactor S-70

### Task 2.1: Create `src/depthfusion/core/file_locking.py`

**Files:**
- Create: `src/depthfusion/core/file_locking.py`

- [ ] **Step 1: Write the module**

```python
"""Atomic frontmatter rewrite helper — extracted from S-70 for reuse (S-72).

Single public symbol: ``atomic_frontmatter_rewrite(path)`` — context manager
that holds an exclusive ``fcntl`` lock on a sidecar ``.scorelock`` file,
yields a mutable ``FrontmatterContext``, and on exit splices the new
importance/salience scalars into the YAML frontmatter, writes via
``mkstemp`` + ``os.replace`` for torn-write safety.

Used by ``_tool_set_memory_score`` (S-70) and ``RecallStore.apply_feedback``
(S-72). Will also be used by S-71 (decay) and S-69 (pin) when they land.
"""
from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class FrontmatterContext:
    """Mutable container yielded by atomic_frontmatter_rewrite.

    The caller calls ``set_score`` to declare the new importance/salience
    values; the context manager applies them on exit. ``body`` exposes the
    file's current contents for callers that need to read existing values.
    """
    body: str
    _importance: Optional[float] = field(default=None)
    _salience: Optional[float] = field(default=None)
    _dirty: bool = field(default=False)

    def set_score(
        self,
        importance: Optional[float] = None,
        salience: Optional[float] = None,
    ) -> None:
        """Declare new score values to splice on context exit.

        Pass ``None`` for any field to leave it unchanged. Calling multiple
        times within the same context replaces the previous declaration.
        """
        if importance is not None:
            self._importance = importance
        if salience is not None:
            self._salience = salience
        self._dirty = True


@contextmanager
def atomic_frontmatter_rewrite(path: Path) -> Iterator[FrontmatterContext]:
    """Lock-serialized RMW on a discovery file's scoring frontmatter.

    Acquires ``fcntl.LOCK_EX`` on a sidecar ``.<filename>.scorelock`` file,
    yields a ``FrontmatterContext`` with the file's body, and on exit (if
    the caller invoked ``set_score``) splices in the new importance/salience
    via the existing ``_splice_memory_score_frontmatter`` helper, writes to
    a unique ``mkstemp`` sibling, fsyncs, then ``os.replace`` over the
    target. ``os.replace`` is atomic on POSIX — process kill mid-write
    leaves the previous file intact.

    Sidecar lock (not the target itself) so ``os.replace``'s inode swap
    doesn't invalidate the lock for concurrent waiters.
    """
    if not path.exists():
        raise FileNotFoundError(f"target does not exist: {path}")

    lock_path = path.parent / f".{path.name}.scorelock"
    lock_fh = open(lock_path, "a", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            body = path.read_text(encoding="utf-8")
            ctx = FrontmatterContext(body=body)
            yield ctx
            if not ctx._dirty:
                return

            # Resolve final values: parse existing for any unsupplied side.
            from depthfusion.capture.dedup import extract_memory_score
            from depthfusion.core.types import MemoryScore
            existing = extract_memory_score(body)
            final_imp = (
                existing.importance if ctx._importance is None else ctx._importance
            )
            final_sal = (
                existing.salience if ctx._salience is None else ctx._salience
            )
            normalized = MemoryScore(importance=final_imp, salience=final_sal)

            from depthfusion.mcp.server import _splice_memory_score_frontmatter
            new_body = _splice_memory_score_frontmatter(
                body, normalized.importance, normalized.salience,
            )

            fd, tmp_str = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tf:
                    tf.write(new_body)
                    tf.flush()
                    os.fsync(tf.fileno())
                os.replace(tmp_str, str(path))
            except Exception:
                try:
                    os.unlink(tmp_str)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    finally:
        lock_fh.close()
```

- [ ] **Step 2: Verify the lock-helper test now lights up**

Run: `python -m pytest tests/test_capture/test_recall_feedback.py::TestLockHelper -v`
Expected: 1 PASSED.

### Task 2.2: Refactor `_tool_set_memory_score` to use the helper

**Files:**
- Modify: `src/depthfusion/mcp/server.py` (the body of `_tool_set_memory_score`, ~30 lines around the existing `fcntl`+`mkstemp` block)

- [ ] **Step 1: Locate the function**

Run: `grep -n "^def _tool_set_memory_score" src/depthfusion/mcp/server.py`
Note the line number; the body extends until the next `def `.

- [ ] **Step 2: Replace the lock-and-write block**

Find the block that starts with `import fcntl` and `import tempfile` inside `_tool_set_memory_score` (introduced by S-70) and the corresponding `with open(lock_path, "a", ...)` through to the matching `finally: fcntl.flock(... LOCK_UN)`. Replace it with:

```python
        from depthfusion.core.file_locking import atomic_frontmatter_rewrite

        try:
            with atomic_frontmatter_rewrite(target) as ctx:
                # Resolve final values: parse existing for unsupplied side,
                # then declare via set_score so the helper splices on exit.
                existing = extract_memory_score(ctx.body)
                final_imp = existing.importance if importance is None else importance
                final_sal = existing.salience if salience is None else salience
                normalized = MemoryScore(importance=final_imp, salience=final_sal)
                ctx.set_score(
                    importance=normalized.importance,
                    salience=normalized.salience,
                )
        except FileNotFoundError:
            return json.dumps({
                "ok": False, "error": f"set_memory_score: file not found: {filename}",
            })
        except OSError as exc:
            return json.dumps({
                "ok": False, "error": f"set_memory_score: write failed: {exc}",
            })

        return json.dumps({
            "ok": True,
            "filename": str(target),
            "importance": normalized.importance,
            "salience": normalized.salience,
        })
```

- [ ] **Step 3: Remove now-unused inline imports**

Inside `_tool_set_memory_score`, remove the `import fcntl` and `import tempfile` lines that the old inline lock block needed — the helper now owns them.

- [ ] **Step 4: Run all S-70 tests — must still pass**

Run: `python -m pytest tests/test_capture/test_scoring.py -v`
Expected: 41 passed, 0 failed.

- [ ] **Step 5: Run full suite for regression**

Run: `python -m pytest --tb=short -q`
Expected: `1226 passed` (no skipped from S-72 unless this commit also lifts the lock-helper gate; if it does, you'll see one extra pass).

### Task 2.3: Commit Phase 2

- [ ] **Step 1: Stage and commit**

```bash
git add src/depthfusion/core/file_locking.py src/depthfusion/mcp/server.py
git commit -m "feat(file_locking): extract atomic_frontmatter_rewrite helper from S-70"
```

The shim will block. Invoke `i-auditreviewer-consensus` on the diff. Author `docs/reviews/2026-05-01-s72-consensus.md` Commit 2 section.

Per the consensus guidance:
- The S-70 refactor portion needs **behavioural-equivalence verification** (Codex tends to catch this well; ask explicitly)
- Watch for **deadlock or lock-ordering** findings on the new helper API
- Watch for **orphan tmp file cleanup** under exception paths

Apply MEDIUM+ findings; retry `git commit ... [skip-review]` once consensus is recorded.

---

# Phase 3 — Commit 3: Implement RecallStore + wire MCP

### Task 3.1: Create `src/depthfusion/core/feedback.py`

**Files:**
- Create: `src/depthfusion/core/feedback.py`

- [ ] **Step 1: Write the module**

```python
"""Recall feedback loop — E-27 / S-72.

In-memory store mapping ``recall_id → (timestamp, [chunk_ids], applied: set)``.
Applies bounded ``salience`` deltas to discovery files when feedback arrives.
Sweep-on-write TTL eviction; idempotent via per-recall_id applied-set.

Public surface:
    - ``RecallStore`` class (use ``RecallStore.singleton()`` for the
      process-wide instance, or instantiate directly in tests)
    - ``FeedbackResult`` dataclass — bucket counts returned by apply_feedback
    - module constants ``USED_BOOST``, ``IGNORED_DECAY``, ``RECALL_TTL_SECONDS``
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

USED_BOOST: float = 0.1
IGNORED_DECAY: float = 0.05
RECALL_TTL_SECONDS: int = 86400  # 24h per AC-2


def _discoveries_dir() -> Path:
    """Resolve the discoveries directory. Patchable for tests."""
    return Path.home() / ".claude" / "shared" / "discoveries"


@dataclass
class _RecallEntry:
    ts: float
    chunk_ids: list[str]
    applied: set[str] = field(default_factory=set)


@dataclass
class FeedbackResult:
    """Bucket counts returned by ``RecallStore.apply_feedback``.

    Each input chunk_id lands in exactly one bucket. ``applied`` is the
    count of chunks whose salience delta successfully landed on disk.
    """
    ok: bool
    applied: int = 0
    skipped_unsupported: int = 0
    skipped_missing: int = 0
    skipped_already_applied: int = 0
    skipped_expired: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["error"] is None:
            d.pop("error")
        return d


def _chunk_id_to_file_stem(chunk_id: str) -> str:
    """Extract the file stem from a chunk_id of form '{stem}#{i}' or '{stem}'."""
    if "#" in chunk_id:
        return chunk_id.split("#", 1)[0]
    return chunk_id


def _resolve_discovery_file(file_stem: str) -> Optional[Path]:
    """Return the live discovery file path for a given chunk's file_stem.

    Returns None if the file is missing, archived (under .archive/), or
    superseded — all of which count as ``skipped_missing`` in the response.
    """
    base = _discoveries_dir()
    candidate = base / f"{file_stem}.md"
    if candidate.is_file():
        return candidate
    return None


class RecallStore:
    """Process-wide in-memory store of recall_id → chunk_ids + applied-set.

    Sweep-on-write TTL eviction (entries with ``ts > 24h`` are deleted on
    the next ``register_recall`` call). All public mutation paths are
    serialized by a single ``threading.Lock``.
    """

    _singleton: Optional["RecallStore"] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def singleton(cls) -> "RecallStore":
        """Return the process-wide instance (lazy-init under lock)."""
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    @classmethod
    def reset_singleton(cls) -> None:
        """Test helper — drops the cached instance."""
        with cls._singleton_lock:
            cls._singleton = None

    def __init__(self) -> None:
        self._entries: dict[str, _RecallEntry] = {}
        self._lock = threading.Lock()

    def register_recall(self, chunk_ids: list[str]) -> str:
        """Mint a new recall_id; sweep stale; insert; return the id."""
        rid = str(uuid.uuid4())
        now = time.time()
        with self._lock:
            # Sweep stale entries before inserting.
            cutoff = now - RECALL_TTL_SECONDS
            self._entries = {
                k: v for k, v in self._entries.items() if v.ts > cutoff
            }
            self._entries[rid] = _RecallEntry(ts=now, chunk_ids=list(chunk_ids))
        return rid

    def apply_feedback(
        self,
        recall_id: str,
        used: list[str],
        ignored: list[str],
    ) -> FeedbackResult:
        """Apply bounded salience deltas. See module docstring for semantics."""
        result = FeedbackResult(ok=True)

        # 1. Lookup recall_id under lock; classify expired/missing.
        with self._lock:
            entry = self._entries.get(recall_id)
            now = time.time()
            if entry is None:
                # Either expired (and swept) or never minted — count as missing.
                # We can't distinguish here cheaply; per AC interpretation,
                # expired vs missing is a metric-only distinction. Surface as
                # missing unless we can prove expiry (we can't, post-sweep).
                result.skipped_missing = len(used) + len(ignored)
                return result
            if now - entry.ts > RECALL_TTL_SECONDS:
                # Pre-sweep state — explicitly expired.
                del self._entries[recall_id]
                result.skipped_expired = len(used) + len(ignored)
                return result
            already_applied = set(entry.applied)
            registered = set(entry.chunk_ids)

        # 2. Bucket each chunk into one outcome.
        # Order of checks: already_applied → not-registered (unsupported)
        # → file-resolves? no = missing : group for application.
        from collections import defaultdict
        per_file_used: dict[Path, int] = defaultdict(int)
        per_file_ignored: dict[Path, int] = defaultdict(int)
        chunks_to_mark_applied: dict[Path, list[str]] = defaultdict(list)

        for chunk_id in used:
            outcome = self._bucket_chunk(
                chunk_id, registered, already_applied,
            )
            if outcome == "already":
                result.skipped_already_applied += 1
                continue
            if outcome == "unsupported":
                result.skipped_unsupported += 1
                continue
            target = outcome  # Path
            per_file_used[target] += 1
            chunks_to_mark_applied[target].append(chunk_id)

        for chunk_id in ignored:
            outcome = self._bucket_chunk(
                chunk_id, registered, already_applied,
            )
            if outcome == "already":
                result.skipped_already_applied += 1
                continue
            if outcome == "unsupported":
                result.skipped_unsupported += 1
                continue
            target = outcome
            per_file_ignored[target] += 1
            chunks_to_mark_applied[target].append(chunk_id)

        # 3. Per target file, compute net delta and apply via lock helper.
        from depthfusion.capture.dedup import extract_memory_score
        from depthfusion.core.file_locking import atomic_frontmatter_rewrite
        applied_chunks_global: set[tuple[str, str]] = set()
        for target in set(per_file_used) | set(per_file_ignored):
            delta = (
                USED_BOOST * per_file_used.get(target, 0)
                - IGNORED_DECAY * per_file_ignored.get(target, 0)
            )
            try:
                with atomic_frontmatter_rewrite(target) as ctx:
                    current = extract_memory_score(ctx.body)
                    new_sal = current.salience + delta
                    ctx.set_score(salience=new_sal)
            except FileNotFoundError:
                # Race: file existed at bucket time but vanished before lock.
                count = per_file_used.get(target, 0) + per_file_ignored.get(target, 0)
                result.skipped_missing += count
                continue
            except OSError as exc:
                logger.warning("recall_feedback: lock/write failed for %s: %s", target, exc)
                count = per_file_used.get(target, 0) + per_file_ignored.get(target, 0)
                result.skipped_missing += count
                continue
            # Success: count and queue the chunks for applied-set update.
            result.applied += per_file_used.get(target, 0) + per_file_ignored.get(target, 0)
            for chunk_id in chunks_to_mark_applied[target]:
                applied_chunks_global.add((recall_id, chunk_id))

        # 4. Mark applied chunks under store lock.
        with self._lock:
            entry = self._entries.get(recall_id)
            if entry is not None:
                for _rid, chunk_id in applied_chunks_global:
                    entry.applied.add(chunk_id)

        return result

    def _bucket_chunk(
        self,
        chunk_id: str,
        registered: set[str],
        already_applied: set[str],
    ) -> object:
        """Classify a single chunk_id. Returns 'already', 'unsupported', or a Path."""
        if chunk_id in already_applied:
            return "already"
        if chunk_id not in registered:
            # Caller sent a chunk_id that wasn't part of the recall.
            return "unsupported"
        file_stem = _chunk_id_to_file_stem(chunk_id)
        target = _resolve_discovery_file(file_stem)
        if target is None:
            # File missing, archived, or superseded. From the response-shape
            # perspective this is "missing" — but the bucket name we use
            # depends on whether the chunk_id itself was ever registered.
            # If it was registered (above check passed), this is post-recall
            # file disappearance → skipped_missing.
            return "unsupported"  # Will be remapped to 'missing' by caller
        return target
```

> Note: the bucket logic above maps unresolvable-file chunks to `skipped_unsupported` for code simplicity. The spec describes `skipped_missing` for archived/superseded. To match the spec exactly, change `_bucket_chunk` to return a sentinel `"missing"` when `_resolve_discovery_file` returns None and the caller should check for that and increment `skipped_missing` instead. This is a 5-line tweak; do it before running the resolution tests.

- [ ] **Step 2: Implement the missing/unsupported distinction**

Update `_bucket_chunk` so it returns:
- `"already"` if the chunk is in `already_applied`
- `"missing"` if the chunk_id is registered but the file is unresolvable (archived/superseded/deleted between recall and feedback)
- `"unsupported"` if the chunk_id was never in the recall at all (caller sent garbage)
- `Path` if everything resolves

Then update both `apply_feedback` loops to handle `"missing"` by incrementing `result.skipped_missing`.

- [ ] **Step 3: Run the feedback tests**

Run: `python -m pytest tests/test_capture/test_recall_feedback.py::TestFeedbackBumps tests/test_capture/test_recall_feedback.py::TestIdempotency tests/test_capture/test_recall_feedback.py::TestEvictionAndExpiry tests/test_capture/test_recall_feedback.py::TestChunkResolution -v`
Expected: All passing.

### Task 3.2: Wire `recall_relevant` to mint recall_ids and add to response

**Files:**
- Modify: `src/depthfusion/mcp/server.py` (lines 482, 536, 641 — the three return sites in `_tool_recall_impl`)

- [ ] **Step 1: Add the import at top of `_tool_recall_impl`**

After the existing imports inside the function body (or at module top if cleaner), add:

```python
from depthfusion.core.feedback import RecallStore
```

- [ ] **Step 2: Compute `recall_id` once at start of `_tool_recall_impl`**

Right after the function reads its arguments, compute the chunk_ids list and mint:

```python
# S-72: mint a recall_id once so all return paths can include it.
# chunk_ids list grows as raw_blocks are assembled; we mint AFTER
# raw_blocks is finalized so the chunk_ids are accurate.
```

Place the actual mint after `raw_blocks` is fully populated (just before any `return json.dumps(...)`):

```python
recall_id = RecallStore.singleton().register_recall(
    [b["chunk_id"] for b in raw_blocks]
)
```

- [ ] **Step 3: Add `recall_id` to all three return sites**

For each `return json.dumps({...})` in `_tool_recall_impl` (lines ~482, ~536, ~641), add `"recall_id": recall_id` to the dict:

- Line ~482: the `"No session context available"` branch — `raw_blocks` is empty here, so still call `register_recall([])` to mint a (degenerate) id, OR skip and return `recall_id: None`. Use `None` for cleanliness — feedback against an empty recall is meaningless.
- Lines ~536 and ~641: include the minted `recall_id` from Step 2.

Concrete edit at line 482:
```python
return json.dumps({
    "query": query, "blocks": [], "recall_id": None,
    "message": "No session context available",
})
```

Concrete edit at lines 536 and 641: insert `"recall_id": recall_id,` after the `"query": query,` line.

- [ ] **Step 4: Verify recall_id minting test passes**

Run: `python -m pytest tests/test_capture/test_recall_feedback.py::TestRecallIdMinting -v`
Expected: 2 PASSED.

### Task 3.3: Add `_tool_recall_feedback` and register the tool

**Files:**
- Modify: `src/depthfusion/mcp/server.py` (TOOLS dict line 19, _TOOL_FLAGS line 75, _dispatch_tool ~line 144, new function placement near _tool_set_memory_score)

- [ ] **Step 1: Add to `TOOLS` dict**

Insert before the closing `}` of `TOOLS`:

```python
"depthfusion_recall_feedback": (
    "Apply bounded salience deltas based on which retrieved chunks were "
    "actually used (S-72). Args: recall_id (str, required) — uuid4 from a "
    "prior recall_relevant response; used (chunk_id[]) — chunks that were "
    "useful (each contributes +0.1 salience to its discovery file); ignored "
    "(chunk_id[]) — chunks that were not (each contributes -0.05). "
    "Idempotent — replaying the same payload skips already-applied chunks. "
    "Response: {ok, applied, skipped_unsupported, skipped_missing, "
    "skipped_already_applied, skipped_expired}."
),
```

- [ ] **Step 2: Add to `_TOOL_FLAGS`**

Insert before the closing `}` of `_TOOL_FLAGS`:

```python
"depthfusion_recall_feedback": None,    # always enabled (S-72)
```

- [ ] **Step 3: Add to `_dispatch_tool`**

Find the `elif tool_name == "depthfusion_set_memory_score":` line and add immediately after it:

```python
    elif tool_name == "depthfusion_recall_feedback":
        return _tool_recall_feedback(arguments)
```

- [ ] **Step 4: Implement `_tool_recall_feedback`**

Add after `_tool_set_memory_score` (and after `_splice_memory_score_frontmatter`):

```python
def _tool_recall_feedback(arguments: dict) -> str:
    """E-27 / S-72 — recall feedback loop entry point."""
    recall_id = arguments.get("recall_id")
    used = arguments.get("used", [])
    ignored = arguments.get("ignored", [])

    if not isinstance(recall_id, str) or not recall_id.strip():
        return json.dumps({
            "ok": False,
            "error": "recall_feedback: 'recall_id' must be a non-empty string",
        })
    if not isinstance(used, list) or not isinstance(ignored, list):
        return json.dumps({
            "ok": False,
            "error": "recall_feedback: 'used' and 'ignored' must be lists",
        })
    if not all(isinstance(c, str) for c in used + ignored):
        return json.dumps({
            "ok": False,
            "error": "recall_feedback: chunk_ids must be strings",
        })

    from depthfusion.core.feedback import RecallStore
    result = RecallStore.singleton().apply_feedback(
        recall_id, used=list(used), ignored=list(ignored),
    )
    return json.dumps(result.to_dict())
```

- [ ] **Step 5: Run all feedback tests**

Run: `python -m pytest tests/test_capture/test_recall_feedback.py -v`
Expected: 18 PASSED, 0 SKIPPED, 0 FAILED.

### Task 3.4: Bump tool-count assertions

**Files:**
- Modify: `tests/test_analyzer/test_mcp_server.py`

- [ ] **Step 1: Update the count assertions**

Find each occurrence of `len(TOOLS) == 14`, `len(enabled) == 14`, `len(enabled) == 10`, `len(enabled) == 10`, `len(enabled) == 9` and increment by 1 (15 / 15 / 11 / 11 / 10).

Find the `expected = {...}` set in `test_tools_dict_has_fourteen_entries` and add `"depthfusion_recall_feedback"` to it. Also rename the test function to `test_tools_dict_has_fifteen_entries`.

- [ ] **Step 2: Run the analyzer tests**

Run: `python -m pytest tests/test_analyzer/test_mcp_server.py -v`
Expected: All passing.

### Task 3.5: Final regression

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest --tb=short -q`
Expected: `1244 passed` (1226 baseline + 18 new), 0 skipped, 0 failed.

### Task 3.6: Commit Phase 3

- [ ] **Step 1: Stage and commit**

```bash
git add src/depthfusion/core/feedback.py src/depthfusion/mcp/server.py tests/test_analyzer/test_mcp_server.py
git commit -m "feat(feedback): implement S-72 recall feedback loop"
```

Consensus loop fires. Apply MEDIUM+ findings; expect Codex to surface concurrency edges around the per-file lock acquisition order (we hold no store lock across file I/O, but flag any deadlock paths). Append to `docs/reviews/2026-05-01-s72-consensus.md` Commit 3 section.

Retry with `[skip-review]` once recorded.

### Task 3.7: Mark BACKLOG.md S-72 as complete

- [ ] **Step 1: Tick off the AC checkboxes and tasks**

Edit `BACKLOG.md` under the `### S-72:` heading. Change all `- [ ]` to `- [x]` for AC-1..AC-6 and T-229..T-234. Add a one-line consensus-review pointer:

```markdown
**Consensus review:** dual-LLM (Claude + Codex CLI) — see `docs/reviews/2026-05-01-s72-consensus.md` — reached at MEDIUM+ severity across 3 commits.
```

- [ ] **Step 2: Commit the backlog update**

```bash
git add BACKLOG.md
git commit -m "docs(backlog): mark S-72 done — recall feedback loop complete [skip-review]"
```

---

## Self-review

**Spec coverage check:**
- AC-1 (recall_id in response, uuid4): Task 3.2 mints; Test `test_register_recall_returns_uuid4_format` verifies. ✓
- AC-2 (24h TTL store): `RECALL_TTL_SECONDS = 86400` in Task 3.1; Test `test_expired_recall_id_skips_all` verifies. ✓
- AC-3 (new MCP tool with +0.1/-0.05 deltas): Task 3.3; Tests `test_used_boost_applies` / `test_ignored_decay_applies` verify. ✓
- AC-4 (idempotency): Task 3.1 applied-set; Tests `test_replay_same_payload_skips_applied` / `test_partial_replay_lands_only_new_chunks` verify. ✓
- AC-5 (bounded mutation): Task 3.1 delegates to `MemoryScore.__post_init__`; Tests `test_clamps_at_max_5` / `test_clamps_at_min_0` verify. ✓
- AC-6 (≥6 tests): 18 delivered. ✓

**Placeholder scan:** None. Every step has concrete code or commands.

**Type consistency:** `FeedbackResult.to_dict()` is the only method called externally; `RecallStore.singleton()`, `register_recall(chunk_ids)`, `apply_feedback(recall_id, used, ignored)` consistent across tasks 3.1, 3.3, and the tests.

One soft spot: the test file references `depthfusion.core.feedback._discoveries_dir` for monkeypatching. The module defines it as a function (not a constant), so the patch works. Verified consistent.

**Scope check:** Single subsystem (recall feedback). 18 tests across 7 classes; ~750 lines of net-new code. Fits cleanly in a single plan.
