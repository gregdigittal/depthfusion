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
