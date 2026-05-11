# tests/test_metrics/test_path_isolation.py
"""S-82 AC-4 / T-276 — MetricsCollector test-vs-production path isolation.

Verifies that the root conftest autouse fixture (_guard_metrics_production_path)
correctly routes default MetricsCollector() writes away from
~/.claude/depthfusion-metrics/ during test runs.

Tests cover:
  1. Default-path constructor is redirected — writes land in a session tmp dir,
     NOT in the real production directory.
  2. Explicit metrics_dir is always respected — the guard does not interfere.
  3. The production directory is never touched by a bare MetricsCollector() call
     during a pytest session.
  4. The autouse fixture is transparent to the existing home-redirect pattern
     used by integration tests.

All tests in this file are hermetic: no writes to ~/.claude/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.metrics.collector import MetricsCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prod_dir() -> Path:
    """Return the real production metrics directory path."""
    return Path.home() / ".claude" / "depthfusion-metrics"


# ---------------------------------------------------------------------------
# AC-4 test 1: guard fires — default MetricsCollector() is redirected away from
# the production path during a pytest run.
# ---------------------------------------------------------------------------

class TestGuardFiresInPytestRun:
    """Default MetricsCollector() must NOT write to ~/.claude/depthfusion-metrics/."""

    def test_default_constructor_does_not_write_to_production_dir(
        self, tmp_path: Path
    ) -> None:
        """A bare MetricsCollector() call is redirected by the autouse fixture.

        The write must land in a temp directory, not in the real production
        directory.  We record a metric and then assert the production dir
        either does not exist or has no new files since the start of this test.
        """
        # Snapshot production dir state before the call
        prod_dir = _prod_dir()
        if prod_dir.exists():
            files_before = set(prod_dir.glob("*.jsonl"))
        else:
            files_before = set()

        # Bare constructor — redirected by the autouse fixture in conftest.py
        collector = MetricsCollector()
        collector.record("test.isolation.check", 1.0)

        # The collector must NOT have written to the production path
        if prod_dir.exists():
            files_after = set(prod_dir.glob("*.jsonl"))
            new_files = files_after - files_before
            assert not new_files, (
                f"Bare MetricsCollector() wrote to the production directory during "
                f"a pytest run.  New file(s): {new_files}.  "
                f"The conftest autouse fixture is not working."
            )

    def test_default_constructor_writes_to_test_temp_dir(self) -> None:
        """The autouse redirect sends default writes to a temp dir, not home.

        The collector's metrics_dir must not be under the real Path.home().
        """
        real_home = Path.home()
        collector = MetricsCollector()

        # The redirected dir must NOT be under the real home
        assert not str(collector.metrics_dir).startswith(str(real_home)), (
            f"MetricsCollector() resolved to {collector.metrics_dir!r} which is "
            f"under the real home {real_home!r}.  "
            f"The autouse guard is not intercepting the default-path case."
        )

    def test_production_directory_absent_or_unmodified_after_default_call(
        self,
    ) -> None:
        """Belt-and-suspenders: verify the production dir is untouched after a
        default-path MetricsCollector() call writes a record.
        """
        prod_dir = _prod_dir()
        files_before = set(prod_dir.glob("*.jsonl")) if prod_dir.exists() else set()

        collector = MetricsCollector()
        collector.record("test.prod.check", 42.0)

        files_after = set(prod_dir.glob("*.jsonl")) if prod_dir.exists() else set()
        new_files = files_after - files_before
        assert not new_files, (
            f"Production dir gained new file(s) after MetricsCollector().record(): "
            f"{new_files}"
        )


# ---------------------------------------------------------------------------
# AC-4 test 2: guard is bypassable — explicit metrics_dir always wins.
# ---------------------------------------------------------------------------

class TestGuardBypassable:
    """Explicit metrics_dir must bypass the autouse redirect entirely."""

    def test_explicit_metrics_dir_is_respected(self, tmp_path: Path) -> None:
        """Passing an explicit metrics_dir bypasses the autouse fixture.

        The collector must write to exactly the directory the caller specified.
        """
        explicit_dir = tmp_path / "my-custom-metrics"
        collector = MetricsCollector(metrics_dir=explicit_dir)

        assert collector.metrics_dir == explicit_dir
        assert collector.metrics_dir.is_dir()

    def test_explicit_metrics_dir_receives_records(self, tmp_path: Path) -> None:
        """Records from an explicit-dir collector land in that directory."""
        explicit_dir = tmp_path / "explicit-metrics"
        collector = MetricsCollector(metrics_dir=explicit_dir)
        collector.record("test.explicit.record", 7.0)

        jsonl_files = list(explicit_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1, (
            f"Expected exactly one JSONL file in {explicit_dir}, got: {jsonl_files}"
        )
        record = json.loads(jsonl_files[0].read_text().strip())
        assert record["metric"] == "test.explicit.record"
        assert record["value"] == 7.0

    def test_explicit_prod_path_bypasses_guard_intentionally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A test can pass the production directory explicitly as an escape hatch.

        This is the documented pattern for integration tests that genuinely
        need to verify behaviour of the production path.  Here we use
        monkeypatch to redirect Path.home() → tmp_path so the test remains
        hermetic.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        prod_path = tmp_path / ".claude" / "depthfusion-metrics"
        # Explicit path bypasses both the autouse redirect AND the home mock.
        collector = MetricsCollector(metrics_dir=prod_path)

        assert collector.metrics_dir == prod_path
        assert collector.metrics_dir.is_dir()

    def test_home_redirect_pattern_works_with_autouse_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The home-redirect pattern used in integration tests still works.

        Many tests do ``monkeypatch.setattr(Path, "home", lambda: tmp_path)``
        and then call production code that internally creates
        ``MetricsCollector()``.  The autouse fixture detects that Path.home()
        has been redirected away from the real home and lets the redirect take
        effect — writes land in ``tmp_path / ".claude" / "depthfusion-metrics"``.

        This test verifies the production code path completes without error AND
        that writes go to the redirected path (not the real production dir).
        """
        # Capture the real home BEFORE monkeypatching so we can assert isolation.
        from tests.conftest import _REAL_HOME

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        # A bare MetricsCollector() call under home redirect — the autouse
        # fixture steps aside and lets the redirect take effect.
        collector = MetricsCollector()
        collector.record("test.home.redirect.compat", 1.0)

        # The write must NOT have gone to the real production dir.
        assert not str(collector.metrics_dir).startswith(str(_REAL_HOME)), (
            f"Collector wrote to {collector.metrics_dir!r} which is under the "
            f"real home {_REAL_HOME!r}.  The autouse fixture must redirect "
            f"default-path writes away from the production directory."
        )
        # The write should be somewhere under tmp_path (the redirected home)
        assert str(collector.metrics_dir).startswith(str(tmp_path)), (
            f"Expected writes to be under tmp_path {tmp_path!r} (redirected home), "
            f"but got {collector.metrics_dir!r}"
        )
