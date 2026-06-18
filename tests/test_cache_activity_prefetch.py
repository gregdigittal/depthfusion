"""Tests for T-652, T-655, T-656 — adaptive offline cache learning cluster.

Covers:
- ActivitySignalStore: on-device signal persistence + privacy invariant
- PrefetchScheduler: idle-time budget-respecting plan builder
- HitRateStore / generate_report: local hit-rate telemetry + dogfood report
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

from depthfusion.cache.activity_signals import (
    _PRIVACY_GUARD,
    ActivitySignalStore,
    SignalKind,
)
from depthfusion.cache.hit_rate import (
    HitRateStore,
    generate_report,
)
from depthfusion.cache.prefetch_scheduler import (
    PrefetchCandidate,
    PrefetchScheduler,
)

# ===========================================================================
# T-652 — Local activity signal store + privacy guard
# ===========================================================================

class TestActivitySignalStore:
    """Unit tests for the on-device activity signal store."""

    @pytest.fixture()
    def store(self) -> ActivitySignalStore:
        return ActivitySignalStore(db_path=":memory:")

    # -----------------------------------------------------------------------
    # Basic persistence
    # -----------------------------------------------------------------------

    def test_record_returns_signal_with_id(self, store: ActivitySignalStore) -> None:
        sig = store.record(SignalKind.QUERY, "search for python async")
        assert sig.signal_id is not None
        assert sig.kind == SignalKind.QUERY
        assert sig.value == "search for python async"

    def test_record_stores_all_fields(self, store: ActivitySignalStore) -> None:
        now = time.time()
        sig = store.record(
            SignalKind.OPEN_DOC,
            "/docs/readme.md",
            project="my-project",
            entity="Alice",
            now=now,
        )
        assert sig.project == "my-project"
        assert sig.entity == "Alice"
        assert sig.ts == pytest.approx(now)

    def test_count_increments_per_record(self, store: ActivitySignalStore) -> None:
        assert store.count() == 0
        store.record(SignalKind.QUERY, "q1")
        store.record(SignalKind.PROJECT, "proj1")
        assert store.count() == 2

    def test_recent_returns_newest_first(self, store: ActivitySignalStore) -> None:
        now = time.time()
        store.record(SignalKind.QUERY, "old", now=now - 100)
        store.record(SignalKind.QUERY, "new", now=now)
        signals = store.recent(limit=2)
        assert signals[0].value == "new"
        assert signals[1].value == "old"

    def test_recent_filter_by_kind(self, store: ActivitySignalStore) -> None:
        store.record(SignalKind.QUERY, "q1")
        store.record(SignalKind.OPEN_DOC, "doc1")
        store.record(SignalKind.QUERY, "q2")
        queries = store.recent(kind=SignalKind.QUERY)
        assert all(s.kind == SignalKind.QUERY for s in queries)
        assert len(queries) == 2

    def test_recent_filter_by_project(self, store: ActivitySignalStore) -> None:
        store.record(SignalKind.QUERY, "q1", project="alpha")
        store.record(SignalKind.QUERY, "q2", project="beta")
        store.record(SignalKind.QUERY, "q3", project="alpha")
        alpha = store.recent(project="alpha")
        assert len(alpha) == 2
        assert all(s.project == "alpha" for s in alpha)

    def test_top_projects_returns_sorted_by_count(self, store: ActivitySignalStore) -> None:
        for _ in range(5):
            store.record(SignalKind.PROJECT, "proj-a", project="alpha")
        for _ in range(2):
            store.record(SignalKind.PROJECT, "proj-b", project="beta")
        top = store.top_projects()
        assert top[0] == ("alpha", 5)
        assert top[1] == ("beta", 2)

    def test_top_values_returns_sorted_by_count(self, store: ActivitySignalStore) -> None:
        for _ in range(3):
            store.record(SignalKind.QUERY, "common query")
        store.record(SignalKind.QUERY, "rare query")
        top = store.top_values(SignalKind.QUERY, limit=2)
        assert top[0] == ("common query", 3)
        assert top[1] == ("rare query", 1)

    def test_clear_removes_all_signals(self, store: ActivitySignalStore) -> None:
        store.record(SignalKind.QUERY, "q")
        store.record(SignalKind.ENTITY, "Alice")
        store.clear()
        assert store.count() == 0

    def test_pruning_enforces_max_signals(self) -> None:
        store = ActivitySignalStore(db_path=":memory:", max_signals=5)
        now = time.time()
        for i in range(8):
            store.record(SignalKind.QUERY, f"q{i}", now=now + i)
        # Should have pruned the oldest to stay at max_signals
        assert store.count() <= 5

    def test_pruning_keeps_newest_signals(self) -> None:
        store = ActivitySignalStore(db_path=":memory:", max_signals=3)
        now = time.time()
        for i in range(6):
            store.record(SignalKind.QUERY, f"q{i}", now=now + i)
        kept = store.recent(limit=10)
        values = [s.value for s in kept]
        # Newest (q5, q4, q3) should be kept
        assert "q5" in values
        assert "q4" in values
        assert "q3" in values

    # -----------------------------------------------------------------------
    # Privacy invariant — T-652 AC-1
    # -----------------------------------------------------------------------

    def test_privacy_guard_constant_is_correct(self) -> None:
        """T-652 AC-1: module exports the privacy sentinel."""
        assert _PRIVACY_GUARD == "on-device-only-never-upload"

    def test_upload_disabled_flag_is_true(self, store: ActivitySignalStore) -> None:
        """T-652 AC-1: upload_disabled flag is always True."""
        assert store.upload_disabled is True

    def test_upload_method_raises_not_implemented(
        self, store: ActivitySignalStore
    ) -> None:
        """T-652 AC-1: calling upload() raises — no upload path exists."""
        with pytest.raises(NotImplementedError, match="on-device"):
            store.upload()  # type: ignore[call-arg]

    def test_sync_to_remote_raises_not_implemented(
        self, store: ActivitySignalStore
    ) -> None:
        """T-652 AC-1: calling sync_to_remote() raises — no upload path exists."""
        with pytest.raises(NotImplementedError, match="on-device"):
            store.sync_to_remote()  # type: ignore[call-arg]

    def test_no_network_import_in_activity_signals_module(self) -> None:
        """T-652 AC-1: the activity_signals module must not import network libs.

        This is the key privacy assertion: if a developer accidentally imports
        ``requests``, ``httpx``, ``urllib.request``, ``http.client``, or
        ``socket`` into the module for upload purposes, this test will catch it
        at CI time before any data can leave the device.

        We check for actual import statements (lines that start with 'import '
        or 'from ... import'), not just string mentions in comments.
        """
        import depthfusion.cache.activity_signals as _mod

        source = inspect.getsource(_mod)

        # Only check lines that are actual imports (not comments or docstrings)
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if (line.strip().startswith("import ") or line.strip().startswith("from "))
            and not line.strip().startswith("#")
        ]

        # Network libraries that must never appear as actual imports
        forbidden_imports = ["requests", "httpx", "aiohttp", "urllib.request", "http.client"]
        for name in forbidden_imports:
            for imp_line in import_lines:
                assert name not in imp_line, (
                    f"Network library '{name}' imported in activity_signals.py: {imp_line!r}. "
                    "Signals must be on-device only — no upload path permitted."
                )

    def test_no_network_import_in_hit_rate_module(self) -> None:
        """T-656 AC-1: the hit_rate module must not import network libs."""
        import depthfusion.cache.hit_rate as _mod

        source = inspect.getsource(_mod)

        import_lines = [
            line.strip()
            for line in source.splitlines()
            if (line.strip().startswith("import ") or line.strip().startswith("from "))
            and not line.strip().startswith("#")
        ]

        forbidden_imports = ["requests", "httpx", "aiohttp", "urllib.request", "http.client"]
        for name in forbidden_imports:
            for imp_line in import_lines:
                assert name not in imp_line, (
                    f"Network library '{name}' imported in hit_rate.py: {imp_line!r}. "
                    "Telemetry must be on-device only."
                )


# ===========================================================================
# T-655 — Idle-time prefetch scheduler
# ===========================================================================

class TestPrefetchScheduler:
    """Unit tests for the idle-time prefetch plan builder."""

    def _candidate(
        self,
        path: str,
        score: float = 1.0,
        size_bytes: int = 1024,
        project: str | None = None,
    ) -> PrefetchCandidate:
        return PrefetchCandidate(
            path=path,
            score=score,
            size_bytes=size_bytes,
            project=project,
        )

    # -----------------------------------------------------------------------
    # Basic plan building
    # -----------------------------------------------------------------------

    def test_empty_candidates_produces_empty_plan(self) -> None:
        sched = PrefetchScheduler(budget_bytes=10_000)
        plan = sched.build_plan([])
        assert plan.selected == []
        assert plan.budget_used_bytes == 0

    def test_all_candidates_fit_within_budget(self) -> None:
        sched = PrefetchScheduler(budget_bytes=10_000)
        candidates = [
            self._candidate("a.txt", score=0.9, size_bytes=1000),
            self._candidate("b.txt", score=0.7, size_bytes=2000),
        ]
        plan = sched.build_plan(candidates)
        assert len(plan.selected) == 2
        assert plan.budget_used_bytes == 3000
        assert plan.skipped_over_budget == []

    def test_candidates_sorted_by_score_descending(self) -> None:
        sched = PrefetchScheduler(budget_bytes=10_000)
        candidates = [
            self._candidate("low.txt", score=0.2, size_bytes=100),
            self._candidate("high.txt", score=0.9, size_bytes=100),
            self._candidate("mid.txt", score=0.5, size_bytes=100),
        ]
        plan = sched.build_plan(candidates)
        # Non-pinned items should be ordered by score descending
        non_pinned = [c for c in plan.selected if not c.pinned]
        scores = [c.score for c in non_pinned]
        assert scores == sorted(scores, reverse=True)

    def test_oversized_candidate_is_skipped(self) -> None:
        sched = PrefetchScheduler(budget_bytes=500)
        candidates = [
            self._candidate("big.txt", score=0.9, size_bytes=1000),  # exceeds budget
            self._candidate("small.txt", score=0.5, size_bytes=100),
        ]
        plan = sched.build_plan(candidates)
        selected_paths = {c.path for c in plan.selected}
        assert "small.txt" in selected_paths
        assert "big.txt" not in selected_paths
        assert any(c.path == "big.txt" for c in plan.skipped_over_budget)

    def test_budget_respected_exactly(self) -> None:
        sched = PrefetchScheduler(budget_bytes=300)
        candidates = [
            self._candidate("a.txt", score=0.9, size_bytes=100),
            self._candidate("b.txt", score=0.8, size_bytes=100),
            self._candidate("c.txt", score=0.7, size_bytes=100),
            self._candidate("d.txt", score=0.6, size_bytes=100),  # would exceed budget
        ]
        plan = sched.build_plan(candidates)
        assert plan.budget_used_bytes <= 300
        assert len(plan.selected) == 3

    # -----------------------------------------------------------------------
    # Pinned items (T-655 AC: "force-includes pinned items")
    # -----------------------------------------------------------------------

    def test_pinned_path_is_force_included(self) -> None:
        sched = PrefetchScheduler(
            budget_bytes=1000,
            pinned_paths=["pinned/doc.md"],
        )
        candidates = [
            self._candidate("pinned/doc.md", score=0.1, size_bytes=100),
            self._candidate("other.txt", score=0.9, size_bytes=200),
        ]
        plan = sched.build_plan(candidates)
        selected_paths = {c.path for c in plan.selected}
        # Pinned item must be in the plan even though its score is low
        assert "pinned/doc.md" in selected_paths

    def test_pinned_project_items_are_force_included(self) -> None:
        sched = PrefetchScheduler(
            budget_bytes=2000,
            pinned_projects=["critical-project"],
        )
        candidates = [
            self._candidate("a.txt", score=0.9, size_bytes=100, project="other"),
            self._candidate("b.txt", score=0.1, size_bytes=100, project="critical-project"),
            self._candidate("c.txt", score=0.2, size_bytes=100, project="critical-project"),
        ]
        plan = sched.build_plan(candidates)
        selected_paths = {c.path for c in plan.selected}
        assert "b.txt" in selected_paths
        assert "c.txt" in selected_paths

    def test_pinned_items_marked_with_pinned_flag(self) -> None:
        sched = PrefetchScheduler(
            budget_bytes=10_000,
            pinned_paths=["pinned.txt"],
        )
        candidates = [
            self._candidate("pinned.txt", score=0.5, size_bytes=100),
            self._candidate("regular.txt", score=0.9, size_bytes=100),
        ]
        plan = sched.build_plan(candidates)
        pinned_in_plan = [c for c in plan.selected if c.pinned]
        assert len(pinned_in_plan) == 1
        assert pinned_in_plan[0].path == "pinned.txt"

    def test_pinned_items_admitted_before_regular_items(self) -> None:
        """Even low-scored pinned items come before high-scored regular items."""
        sched = PrefetchScheduler(
            budget_bytes=200,  # tight budget
            pinned_paths=["pinned.txt"],
        )
        candidates = [
            self._candidate("pinned.txt", score=0.01, size_bytes=150),
            self._candidate("high.txt", score=0.99, size_bytes=150),
        ]
        plan = sched.build_plan(candidates)
        # Budget = 200; pinned.txt (150) fits, then high.txt (150) doesn't
        selected_paths = {c.path for c in plan.selected}
        assert "pinned.txt" in selected_paths
        # high.txt should be in skipped_over_budget
        assert any(c.path == "high.txt" for c in plan.skipped_over_budget)

    def test_plan_reports_pinned_and_scored_counts(self) -> None:
        sched = PrefetchScheduler(
            budget_bytes=10_000,
            pinned_paths=["p1.txt", "p2.txt"],
        )
        candidates = [
            self._candidate("p1.txt", score=0.5, size_bytes=100),
            self._candidate("p2.txt", score=0.5, size_bytes=100),
            self._candidate("r1.txt", score=0.9, size_bytes=100),
            self._candidate("r2.txt", score=0.8, size_bytes=100),
        ]
        plan = sched.build_plan(candidates)
        assert plan.pinned_count == 2
        assert plan.scored_count == 2

    # -----------------------------------------------------------------------
    # Idle detection
    # -----------------------------------------------------------------------

    def test_default_idle_fn_returns_true(self) -> None:
        sched = PrefetchScheduler()
        assert sched.is_idle() is True

    def test_custom_idle_fn_is_honoured(self) -> None:
        call_count = 0

        def fake_idle() -> bool:
            nonlocal call_count
            call_count += 1
            return False

        sched = PrefetchScheduler(idle_fn=fake_idle)
        assert sched.is_idle() is False
        assert call_count == 1

    # -----------------------------------------------------------------------
    # Pin management API
    # -----------------------------------------------------------------------

    def test_add_pin_adds_to_pinned_paths(self) -> None:
        sched = PrefetchScheduler()
        sched.add_pin("new/path.md")
        assert "new/path.md" in sched.pinned_paths

    def test_remove_pin_removes_from_pinned_paths(self) -> None:
        sched = PrefetchScheduler(pinned_paths=["to-remove.txt"])
        sched.remove_pin("to-remove.txt")
        assert "to-remove.txt" not in sched.pinned_paths

    def test_add_pinned_project_adds_to_set(self) -> None:
        sched = PrefetchScheduler()
        sched.add_pinned_project("my-proj")
        assert "my-proj" in sched.pinned_projects

    def test_remove_pinned_project_removes_from_set(self) -> None:
        sched = PrefetchScheduler(pinned_projects=["to-remove"])
        sched.remove_pinned_project("to-remove")
        assert "to-remove" not in sched.pinned_projects

    # -----------------------------------------------------------------------
    # Budget-used reporting
    # -----------------------------------------------------------------------

    def test_budget_used_bytes_sums_selected_sizes(self) -> None:
        sched = PrefetchScheduler(budget_bytes=10_000)
        candidates = [
            self._candidate("a.txt", score=0.9, size_bytes=300),
            self._candidate("b.txt", score=0.8, size_bytes=500),
        ]
        plan = sched.build_plan(candidates)
        assert plan.budget_used_bytes == 800


# ===========================================================================
# T-656 — Offline hit-rate telemetry + dogfood report
# ===========================================================================

class TestHitRateStore:
    """Unit tests for the local hit-rate telemetry store."""

    @pytest.fixture()
    def store(self) -> HitRateStore:
        return HitRateStore(db_path=":memory:")

    def test_no_events_hit_rate_is_zero(self, store: HitRateStore) -> None:
        report = store.compute()
        assert report.hit_rate == 0.0
        assert report.total_lookups == 0

    def test_all_hits_rate_is_one(self, store: HitRateStore) -> None:
        now = time.time()
        for _ in range(5):
            store.record_hit(now=now)
        report = store.compute(now=now)
        assert report.hit_rate == pytest.approx(1.0)

    def test_all_misses_rate_is_zero(self, store: HitRateStore) -> None:
        now = time.time()
        for _ in range(3):
            store.record_miss(now=now)
        report = store.compute(now=now)
        assert report.hit_rate == 0.0

    def test_mixed_hits_and_misses(self, store: HitRateStore) -> None:
        now = time.time()
        for _ in range(8):
            store.record_hit(now=now)
        for _ in range(2):
            store.record_miss(now=now)
        report = store.compute(now=now)
        assert report.hit_rate == pytest.approx(0.8)
        assert report.total_hits == 8
        assert report.total_misses == 2
        assert report.total_lookups == 10

    def test_window_filters_old_events(self, store: HitRateStore) -> None:
        now = time.time()
        # Old hits (1 hour ago) — outside window
        for _ in range(5):
            store.record_hit(now=now - 3600)
        # Recent hits (within 5 minutes) — inside window
        for _ in range(3):
            store.record_hit(now=now - 60)
        # Recent misses
        for _ in range(7):
            store.record_miss(now=now - 60)

        # Window = last 10 minutes
        report = store.compute(window_seconds=600, now=now)
        assert report.total_hits == 3
        assert report.total_misses == 7
        assert report.hit_rate == pytest.approx(0.3)

    def test_clear_removes_all_events(self, store: HitRateStore) -> None:
        store.record_hit()
        store.record_miss()
        store.clear()
        assert store.total_events() == 0

    def test_upload_disabled_flag_is_true(self, store: HitRateStore) -> None:
        """T-656: telemetry is never uploaded."""
        assert store.upload_disabled is True

    def test_total_events_counts_hits_and_misses(self, store: HitRateStore) -> None:
        store.record_hit()
        store.record_hit()
        store.record_miss()
        assert store.total_events() == 3


class TestGenerateReport:
    """Tests for the dogfood report generator."""

    @pytest.fixture()
    def store(self) -> HitRateStore:
        return HitRateStore(db_path=":memory:")

    def test_report_is_markdown_string(self, store: HitRateStore) -> None:
        report_md = generate_report(store)
        assert isinstance(report_md, str)
        assert report_md.startswith("#")

    def test_report_contains_hit_rate(self, store: HitRateStore) -> None:
        now = time.time()
        for _ in range(8):
            store.record_hit(now=now)
        for _ in range(2):
            store.record_miss(now=now)
        report_md = generate_report(store, now=now)
        assert "80.0%" in report_md

    def test_report_contains_privacy_notice(self, store: HitRateStore) -> None:
        report_md = generate_report(store)
        # Privacy notice must be in the report
        assert "never uploaded" in report_md.lower() or "on-device" in report_md.lower()

    def test_report_target_met_shows_pass(self, store: HitRateStore) -> None:
        now = time.time()
        for _ in range(90):
            store.record_hit(now=now)
        for _ in range(10):
            store.record_miss(now=now)
        report_md = generate_report(store, now=now)
        assert "PASS" in report_md

    def test_report_target_not_met_shows_fail(self, store: HitRateStore) -> None:
        now = time.time()
        for _ in range(5):
            store.record_hit(now=now)
        for _ in range(5):
            store.record_miss(now=now)
        report_md = generate_report(store, now=now)
        assert "FAIL" in report_md

    def test_report_custom_title(self, store: HitRateStore) -> None:
        report_md = generate_report(store, title="My Custom Report")
        assert "My Custom Report" in report_md

    def test_empty_store_report_warns_no_events(self, store: HitRateStore) -> None:
        report_md = generate_report(store)
        assert "No cache events" in report_md or "Warning" in report_md

    def test_report_can_be_written_to_file(
        self, store: HitRateStore, tmp_path: Path
    ) -> None:
        now = time.time()
        for _ in range(5):
            store.record_hit(now=now)
        store.record_miss(now=now)
        report_md = generate_report(store, now=now)
        out = tmp_path / "dogfood-report.md"
        out.write_text(report_md, encoding="utf-8")
        assert out.exists()
        assert len(out.read_text()) > 50
