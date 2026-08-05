# tests/conftest.py
"""Root pytest configuration for the depthfusion test suite.

Test-vs-production path separation (S-82)
==========================================
``MetricsCollector()`` called with no ``metrics_dir`` resolves to
``~/.claude/depthfusion-metrics/`` by default.  Over the 13-day dogfood
window, 987/987 observed telemetry events (100%) were test-fixture writes
to that production directory.

This conftest provides an **autouse session fixture** that patches
``MetricsCollector.__init__`` so any call *without* an explicit
``metrics_dir`` is transparently redirected to a per-test-session temporary
directory instead of the real production path, but **only when
``Path.home()`` resolves to the real user home directory**.

If a test has already redirected ``Path.home()`` (the common integration-test
pattern), the redirect is a different path and we let that test's isolation
mechanism take effect as intended.

Design principles
-----------------
* **Zero test-file changes required.**  Existing tests that use the
  ``monkeypatch.setattr(Path, "home", ...)`` isolation pattern continue to
  work — the patch inspects the resolved home and only overrides the default
  when the REAL home would be used.
* **Explicit overrides always win.**  Passing a non-None ``metrics_dir``
  bypasses the redirect entirely.
* **Guard is in test infra, not production code.**  ``MetricsCollector``
  itself remains unmodified; the guard lives here where it belongs.

See tests/README.md for the full separation policy and escape hatches.

Fabric store / EventStore production-path guard (T-849)
=========================================================
``mcp/tools/_state.py::_get_fabric_store()`` is a lazy singleton that,
absent an explicit override, resolves to ``JSONGraphStore``/
``SQLiteGraphStore``'s own default — ``~/.claude/depthfusion-graph.json`` or
``~/.claude/depthfusion-graph.db`` under the *real* developer home
directory (see ``graph/store.py``). T-849 wired an ambient-trace publish
side effect into ``mcp/server.py::_process_request`` that fires on every
``tools/call`` request; any test exercising ``_process_request`` /
``_handle_tools_call`` / any of the several tools that call
``_get_fabric_store()`` directly, *without itself* monkeypatching that
lookup, would otherwise background-write test-fixture ``ambient_trace``
(and other) events into that real, on-disk production knowledge graph —
the exact S-82 class of pollution the metrics guard above exists to
prevent, now via a second production path. The ``_guard_fabric_store_
production_path`` fixture below applies the identical pattern to close it.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# The real home directory captured at import time, before any test can
# monkey-patch Path.home().  Used to detect when a test has redirected home.
_REAL_HOME: Path = Path.home()


@pytest.fixture(autouse=True, scope="session")
def _guard_metrics_production_path(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Redirect default MetricsCollector() writes away from ~/.claude/.

    Any ``MetricsCollector()`` call that does **not** supply an explicit
    ``metrics_dir`` AND whose ``Path.home()`` still resolves to the *real*
    home directory is patched to write to a shared per-session temp
    directory instead.

    Tests that monkey-patch ``Path.home()`` to a temp dir (the common
    integration-test isolation pattern) are unaffected — the home redirect
    is a different directory from ``_REAL_HOME``, so the guard steps aside
    and the test's isolation mechanism takes effect as intended.
    """
    from depthfusion.metrics.collector import MetricsCollector

    # Per-session temp dir: shared across all tests but isolated from production.
    session_metrics = tmp_path_factory.mktemp("session_metrics", numbered=True)

    original_init = MetricsCollector.__init__

    def _safe_init(
        self: MetricsCollector,
        metrics_dir: Path | None = None,
        **kwargs,
    ) -> None:
        # S-81 added the `config_version_resolver` keyword on
        # MetricsCollector.__init__; forward all extra kwargs through to
        # the original init so injected resolvers (and any future
        # additions) keep working under this guard.
        if metrics_dir is None:
            resolved_home = Path.home()
            if resolved_home == _REAL_HOME:
                # No home redirect active — intercept and route to session temp dir.
                metrics_dir = session_metrics / "depthfusion-metrics"
            else:
                # A test has redirected Path.home() — let that test's isolation
                # mechanism work as intended.
                metrics_dir = resolved_home / ".claude" / "depthfusion-metrics"
        original_init(self, metrics_dir=metrics_dir, **kwargs)

    with patch.object(MetricsCollector, "__init__", _safe_init):
        yield


@pytest.fixture(autouse=True, scope="session")
def _guard_fabric_store_production_path(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Redirect the lazy fabric-store singleton away from ~/.claude/ (T-849).

    ``mcp/tools/_state.py::_get_fabric_store()`` lazily constructs an
    ``EventStore`` over ``graph/store.py::get_store()``'s default backend,
    which — absent an explicit path — resolves to
    ``~/.claude/depthfusion-graph.json`` (local mode) or
    ``~/.claude/depthfusion-graph.db`` (vps mode) under the *real* developer
    home directory. T-849 wired an ambient-trace publish side effect into
    ``mcp/server.py::_process_request`` that fires on every ``tools/call``
    request, so any test that exercises ``_process_request`` /
    ``_handle_tools_call`` without itself monkeypatching
    ``_get_fabric_store`` would otherwise write test-fixture events into
    that real, on-disk production knowledge graph — confirmed during T-849
    implementation (``tests/test_mcp_authz.py``'s un-mocked dispatch tests
    wrote six ``ambient_trace`` entities into the live
    ``~/.claude/depthfusion-graph.db`` before this guard was added; they
    were manually purged and this fixture closes the gap).

    Same pattern as ``_guard_metrics_production_path`` above: intercepts
    only when ``Path.home()`` still resolves to the real home directory, so
    tests that already redirect home or explicitly monkeypatch
    ``_get_fabric_store`` themselves are unaffected — a per-test
    ``monkeypatch.setattr(...)`` always overrides this session-scoped
    default for the duration of that test, then reverts back to it
    afterwards (monkeypatch restores whatever value was in place
    immediately before it patched, which is this fixture's replacement,
    not the original production function).
    """
    import depthfusion.mcp.tools._state as _mcp_state
    from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
    from depthfusion.graph.store import JSONGraphStore, get_store

    # Per-session temp dir: shared across all tests but isolated from production.
    session_graph_path = (
        tmp_path_factory.mktemp("session_fabric_store", numbered=True)
        / "depthfusion-graph.json"
    )

    def _session_fabric_store() -> EventStore:
        if Path.home() == _REAL_HOME:
            # No home redirect active — intercept and route to a session
            # temp file instead of the real ~/.claude/ graph store.
            return EventStore(
                graph=JSONGraphStore(path=session_graph_path),
                stream=InMemoryStreamBackend(),
            )
        # A test has redirected Path.home() — let production code's own
        # resolution (against that redirected home) work as intended.
        return EventStore(graph=get_store(), stream=None)

    with patch.object(_mcp_state, "_get_fabric_store", _session_fabric_store):
        yield
