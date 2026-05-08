# tests/test_metrics/test_config_version_id.py
"""S-81 / T-271..T-273 — config_version_id population on structured events.

Background: the week-1 dogfood report found `config_version_id` was
empty string in 100% of capture & recall events (957/957 capture,
30/30 recall observed over 13 days). Per DR-018 §4 the field is the
auditor-reproducibility token; empty string defeats the guarantee.

AC-1 — `record_capture_event` and `record_recall_query` populate
       `config_version_id` from the runtime-config resolver when not
       explicitly supplied.
AC-2 — different runtime configurations (env snapshots) produce
       different ids; identical configs produce identical ids.
AC-3 — passing the documented `CONFIG_VERSION_NONE` sentinel preserves
       it verbatim; empty string is never written to disk.
AC-4 — ≥ 3 tests covering capture-path, recall-path, sentinel case.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.metrics.collector import (
    CONFIG_VERSION_NONE,
    MetricsCollector,
    _RUNTIME_CONFIG_ENV_KEYS,
    _runtime_config_version_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_one(path: Path) -> dict:
    """Read the first JSONL line from `path`."""
    return json.loads(path.read_text().splitlines()[0])


def _read_all(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines()]


def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every tracked DEPTHFUSION_* env var so tests start from a
    known baseline. Without this a host export of `DEPTHFUSION_MODE` (etc.)
    would change the resolver's output across CI environments.
    """
    for k in _RUNTIME_CONFIG_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# AC-1: capture-path population from the runtime resolver
# ---------------------------------------------------------------------------


class TestCapturePathPopulation:
    def test_capture_event_populates_config_version_id_by_default(
        self, tmp_path, monkeypatch
    ):
        """No explicit config_version_id → resolver runs → non-empty id."""
        _clear_runtime_env(monkeypatch)
        c = MetricsCollector(tmp_path)
        c.record_capture_event(
            capture_mechanism="decision_extractor",
            project="depthfusion",
            entries_written=1,
        )
        entry = _read_one(c.today_capture_path())
        # The dogfood-week regression: this used to be "".
        assert entry["config_version_id"] != ""
        # And it's not the sentinel either — the resolver produced a real id.
        assert entry["config_version_id"] != CONFIG_VERSION_NONE
        # 12-char hex (sha256 prefix) per the runtime hash contract.
        assert len(entry["config_version_id"]) == 12
        int(entry["config_version_id"], 16)  # raises if not hex

    def test_capture_event_uses_injected_resolver(self, tmp_path):
        """A constructor-injected resolver overrides the default."""
        c = MetricsCollector(
            tmp_path,
            config_version_resolver=lambda: "deadbeef0001",
        )
        c.record_capture_event(
            capture_mechanism="decision_extractor",
            project="p",
        )
        entry = _read_one(c.today_capture_path())
        assert entry["config_version_id"] == "deadbeef0001"

    def test_capture_event_explicit_id_preserved(self, tmp_path, monkeypatch):
        """A non-empty explicit config_version_id is preserved verbatim
        and the resolver is NOT consulted (gate-config callers can
        propagate `GateConfig.version_id()` if they want).
        """
        _clear_runtime_env(monkeypatch)
        sentinel_calls = {"n": 0}

        def _explode() -> str:
            sentinel_calls["n"] += 1
            raise RuntimeError("resolver should not be called")

        c = MetricsCollector(tmp_path, config_version_resolver=_explode)
        c.record_capture_event(
            capture_mechanism="dedup",
            project="p",
            config_version_id="abc123def456",
        )
        entry = _read_one(c.today_capture_path())
        assert entry["config_version_id"] == "abc123def456"
        assert sentinel_calls["n"] == 0

    def test_capture_event_empty_string_falls_through_to_resolver(
        self, tmp_path
    ):
        """Empty string is not a valid output. Caller passes "" → resolver runs."""
        c = MetricsCollector(
            tmp_path,
            config_version_resolver=lambda: "abcdef012345",
        )
        c.record_capture_event(
            capture_mechanism="dedup",
            project="p",
            config_version_id="",
        )
        entry = _read_one(c.today_capture_path())
        assert entry["config_version_id"] == "abcdef012345"

    def test_capture_event_resolver_failure_emits_sentinel(self, tmp_path):
        """A throwing resolver must not break observability."""

        def _raises() -> str:
            raise RuntimeError("resolver kaput")

        c = MetricsCollector(tmp_path, config_version_resolver=_raises)
        c.record_capture_event(capture_mechanism="dedup", project="p")
        entry = _read_one(c.today_capture_path())
        assert entry["config_version_id"] == CONFIG_VERSION_NONE


# ---------------------------------------------------------------------------
# AC-1: recall-path population from the runtime resolver
# ---------------------------------------------------------------------------


class TestRecallPathPopulation:
    def test_recall_event_populates_config_version_id_by_default(
        self, tmp_path, monkeypatch
    ):
        """No explicit config_version_id → resolver runs → non-empty id."""
        _clear_runtime_env(monkeypatch)
        c = MetricsCollector(tmp_path)
        c.record_recall_query(
            query_hash="q12345",
            mode="local",
            backend_used={"reranker": "null"},
            latency_ms_per_capability={"reranker": 1.0},
            total_latency_ms=2.0,
            result_count=0,
        )
        entry = _read_one(c.today_recall_path())
        assert entry["config_version_id"] != ""
        assert entry["config_version_id"] != CONFIG_VERSION_NONE
        assert len(entry["config_version_id"]) == 12
        int(entry["config_version_id"], 16)

    def test_recall_event_uses_injected_resolver(self, tmp_path):
        c = MetricsCollector(
            tmp_path,
            config_version_resolver=lambda: "feedface0123",
        )
        c.record_recall_query(query_hash="q")
        entry = _read_one(c.today_recall_path())
        assert entry["config_version_id"] == "feedface0123"

    def test_recall_event_explicit_id_preserved(self, tmp_path):
        """An explicit non-empty id (e.g. from GateConfig.version_id())
        bypasses the resolver and lands on disk verbatim.
        """
        c = MetricsCollector(
            tmp_path,
            config_version_resolver=lambda: "should-not-be-used",
        )
        c.record_recall_query(
            query_hash="q",
            config_version_id="cafebabe9999",
        )
        entry = _read_one(c.today_recall_path())
        assert entry["config_version_id"] == "cafebabe9999"

    def test_recall_event_does_not_regress_per_capability_latency(
        self, tmp_path, monkeypatch
    ):
        """S-80 invariant: per-capability latency dict is still emitted
        intact even though S-81 added resolver wiring on the same path.
        """
        _clear_runtime_env(monkeypatch)
        c = MetricsCollector(tmp_path)
        c.record_recall_query(
            query_hash="q",
            mode="local",
            backend_used={"reranker": "null", "embedding": "local"},
            latency_ms_per_capability={"reranker": 12.5, "embedding": 3.25},
            total_latency_ms=20.0,
            result_count=4,
        )
        entry = _read_one(c.today_recall_path())
        assert entry["latency_ms_per_capability"] == {
            "reranker": 12.5,
            "embedding": 3.25,
        }
        # And S-81 wiring is also active on this same record.
        assert entry["config_version_id"] not in ("", CONFIG_VERSION_NONE)


# ---------------------------------------------------------------------------
# AC-3: "non-applicable" sentinel case
# ---------------------------------------------------------------------------


class TestSentinelCase:
    def test_explicit_sentinel_preserved_on_capture(self, tmp_path):
        """Genuinely config-invariant emissions pass `CONFIG_VERSION_NONE`
        and that exact value lands on disk (the resolver is bypassed).
        """
        c = MetricsCollector(
            tmp_path,
            config_version_resolver=lambda: "should-not-be-used",
        )
        c.record_capture_event(
            capture_mechanism="dedup",
            project="p",
            config_version_id=CONFIG_VERSION_NONE,
        )
        entry = _read_one(c.today_capture_path())
        assert entry["config_version_id"] == "none"
        assert entry["config_version_id"] == CONFIG_VERSION_NONE

    def test_explicit_sentinel_preserved_on_recall(self, tmp_path):
        c = MetricsCollector(
            tmp_path,
            config_version_resolver=lambda: "should-not-be-used",
        )
        c.record_recall_query(
            query_hash="q",
            config_version_id=CONFIG_VERSION_NONE,
        )
        entry = _read_one(c.today_recall_path())
        assert entry["config_version_id"] == "none"

    def test_resolver_returning_empty_coerces_to_sentinel(self, tmp_path):
        """A resolver that returns "" or None must not produce empty
        string on disk. Emit the documented sentinel instead.
        """
        c_empty = MetricsCollector(
            tmp_path / "empty",
            config_version_resolver=lambda: "",
        )
        c_empty.record_capture_event(capture_mechanism="dedup", project="p")
        entry = _read_one(c_empty.today_capture_path())
        assert entry["config_version_id"] == CONFIG_VERSION_NONE

        c_none = MetricsCollector(
            tmp_path / "none",
            config_version_resolver=lambda: None,  # type: ignore[arg-type,return-value]
        )
        c_none.record_recall_query(query_hash="q")
        entry = _read_one(c_none.today_recall_path())
        assert entry["config_version_id"] == CONFIG_VERSION_NONE

    def test_empty_string_never_written_to_disk_under_any_path(
        self, tmp_path, monkeypatch
    ):
        """Belt-and-braces invariant scan: across the matrix of
        (default resolver, injected non-empty, injected empty,
         caller-empty, caller-sentinel), no emitted record carries
        config_version_id == "".
        """
        _clear_runtime_env(monkeypatch)

        # Default resolver — uses runtime env snapshot.
        c1 = MetricsCollector(tmp_path / "c1")
        c1.record_capture_event(capture_mechanism="dedup", project="p")
        c1.record_recall_query(query_hash="q")

        # Injected resolver returning a real id.
        c2 = MetricsCollector(
            tmp_path / "c2", config_version_resolver=lambda: "id123"
        )
        c2.record_capture_event(capture_mechanism="dedup", project="p")
        c2.record_recall_query(query_hash="q")

        # Injected resolver returning empty.
        c3 = MetricsCollector(
            tmp_path / "c3", config_version_resolver=lambda: ""
        )
        c3.record_capture_event(capture_mechanism="dedup", project="p")
        c3.record_recall_query(query_hash="q")

        # Explicit caller passes empty string.
        c4 = MetricsCollector(
            tmp_path / "c4", config_version_resolver=lambda: "id456"
        )
        c4.record_capture_event(
            capture_mechanism="dedup", project="p", config_version_id=""
        )
        c4.record_recall_query(query_hash="q", config_version_id="")

        # Explicit caller passes sentinel.
        c5 = MetricsCollector(
            tmp_path / "c5", config_version_resolver=lambda: "id789"
        )
        c5.record_capture_event(
            capture_mechanism="dedup",
            project="p",
            config_version_id=CONFIG_VERSION_NONE,
        )
        c5.record_recall_query(
            query_hash="q", config_version_id=CONFIG_VERSION_NONE
        )

        for c in (c1, c2, c3, c4, c5):
            for entry in _read_all(c.today_capture_path()):
                assert entry["config_version_id"] != ""
                assert entry["config_version_id"] is not None
            for entry in _read_all(c.today_recall_path()):
                assert entry["config_version_id"] != ""
                assert entry["config_version_id"] is not None


# ---------------------------------------------------------------------------
# AC-2: deterministic across processes; differs across configs
# ---------------------------------------------------------------------------


class TestRuntimeConfigDeterminism:
    def test_same_env_snapshot_produces_same_id(self, monkeypatch):
        """Two calls under identical env produce identical ids — the
        contract that an auditor relies on to reproduce a recall result.
        """
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "haiku")
        a = _runtime_config_version_id()
        b = _runtime_config_version_id()
        assert a == b
        assert len(a) == 12
        int(a, 16)

    def test_different_env_snapshots_produce_different_ids(self, monkeypatch):
        """Flipping a tracked var changes the id — what makes
        config_version_id a useful pin for "which config produced this".
        """
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        local_id = _runtime_config_version_id()

        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
        vps_id = _runtime_config_version_id()

        assert local_id != vps_id

    def test_changing_backend_mix_changes_id(self, monkeypatch):
        _clear_runtime_env(monkeypatch)
        monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "haiku")
        a = _runtime_config_version_id()
        monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "gemma")
        b = _runtime_config_version_id()
        assert a != b

    def test_unset_env_baseline_is_deterministic_sentinel_value(
        self, monkeypatch
    ):
        """With no tracked env vars set at all, the resolver still
        produces a stable id (the hash of all-empty values). This is
        the production-ish baseline for a fresh `~/.claude/depthfusion.env`
        and must be the same across hosts.
        """
        _clear_runtime_env(monkeypatch)
        a = _runtime_config_version_id()
        b = _runtime_config_version_id()
        assert a == b
        assert len(a) == 12
