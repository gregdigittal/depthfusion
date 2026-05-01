"""Tests for S-71 — bucketed salience decay.

≥ 5 tests required by S-71 AC. This file delivers 9 tests covering every
bucket (pinned, high, mid, low), idempotency, hard-archive, and env-override.

Tests are written to be independent: each builds a fresh ``tmp_path`` fixture
and uses ``monkeypatch`` so env-var changes don't bleed between tests.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from depthfusion.capture.decay import apply_decay, DecaySummary


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_discovery(
    path: Path,
    *,
    importance: float = 0.5,
    salience: float = 1.0,
    pinned: bool = False,
    last_decay_date: str | None = None,
) -> Path:
    """Write a minimal discovery file with the given frontmatter scalars."""
    extra = ""
    if pinned:
        extra += "pinned: true\n"
    if last_decay_date is not None:
        extra += f"last_decay_date: {last_decay_date}\n"
    path.write_text(
        "---\n"
        f"project: testproj\n"
        "session_id: test-sess\n"
        "type: decisions\n"
        f"importance: {importance:.4f}\n"
        f"salience: {salience:.4f}\n"
        f"{extra}"
        "---\n"
        "\n# Decisions\n- A test decision\n",
        encoding="utf-8",
    )
    return path


def _read_salience(path: Path) -> float:
    body = path.read_text(encoding="utf-8")
    m = re.search(r"^salience:\s*([\d.]+)", body, re.MULTILINE)
    assert m is not None, f"salience not found in {path}"
    return float(m.group(1))


def _read_last_decay_date(path: Path) -> str | None:
    body = path.read_text(encoding="utf-8")
    m = re.search(r"^last_decay_date:\s*(\S+)", body, re.MULTILINE)
    return m.group(1) if m else None


# ------------------------------------------------------------------
# Test 1 — pinned bucket: salience must not change
# ------------------------------------------------------------------

class TestPinnedBucket:
    def test_pinned_file_skipped(self, tmp_path: Path) -> None:
        """A file with ``pinned: true`` must not be touched by the decay job."""
        disc = _write_discovery(
            tmp_path / "2026-01-01-pinned.md",
            importance=0.9,
            salience=4.0,
            pinned=True,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.skipped_pinned == 1
        assert summary.decayed == 0
        assert _read_salience(disc) == pytest.approx(4.0, abs=1e-4), \
            "pinned file salience must not change"


# ------------------------------------------------------------------
# Test 2 — HIGH bucket (importance >= 0.8): 1%/day decay
# ------------------------------------------------------------------

class TestHighBucket:
    def test_high_importance_applies_1pct_per_day(self, tmp_path: Path) -> None:
        """importance=0.85 → HIGH bucket → new_salience = 2.0 * 0.99 ** 1."""
        disc = _write_discovery(
            tmp_path / "2026-01-01-high.md",
            importance=0.85,
            salience=2.0,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.decayed == 1
        expected = 2.0 * 0.99
        assert _read_salience(disc) == pytest.approx(expected, abs=1e-4), \
            f"HIGH bucket should apply 1%/day: expected {expected:.4f}"
        assert _read_last_decay_date(disc) == "2026-05-01"


# ------------------------------------------------------------------
# Test 3 — MID bucket (0.5 <= importance < 0.8): 2%/day decay
# ------------------------------------------------------------------

class TestMidBucket:
    def test_mid_importance_applies_2pct_per_day(self, tmp_path: Path) -> None:
        """importance=0.65 → MID bucket → new_salience = 3.0 * 0.98 ** 1."""
        disc = _write_discovery(
            tmp_path / "2026-01-01-mid.md",
            importance=0.65,
            salience=3.0,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.decayed == 1
        expected = 3.0 * 0.98
        assert _read_salience(disc) == pytest.approx(expected, abs=1e-4), \
            f"MID bucket should apply 2%/day: expected {expected:.4f}"


# ------------------------------------------------------------------
# Test 4 — LOW bucket (importance < 0.5): 5%/day decay
# ------------------------------------------------------------------

class TestLowBucket:
    def test_low_importance_applies_5pct_per_day(self, tmp_path: Path) -> None:
        """importance=0.3 → LOW bucket → new_salience = 1.0 * 0.95 ** 1."""
        disc = _write_discovery(
            tmp_path / "2026-01-01-low.md",
            importance=0.3,
            salience=1.0,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.decayed == 1
        expected = 1.0 * 0.95
        assert _read_salience(disc) == pytest.approx(expected, abs=1e-4), \
            f"LOW bucket should apply 5%/day: expected {expected:.4f}"


# ------------------------------------------------------------------
# Test 5 — Hard-archive: salience < threshold → file moved to .archive/
# ------------------------------------------------------------------

class TestHardArchive:
    def test_file_archived_when_salience_drops_below_threshold(
        self, tmp_path: Path
    ) -> None:
        """A LOW-importance file with salience just above threshold decays below
        it in one pass and must be moved to ``.archive/``, not modified in place."""
        # salience = 0.06, LOW rate = 5%/day → 0.06 * 0.95 = 0.057
        # But let's use a value that drops below 0.05 directly:
        # salience = 0.051 → 0.051 * 0.95 ≈ 0.0485 < 0.05 → archived
        disc = _write_discovery(
            tmp_path / "2026-01-01-near-archive.md",
            importance=0.3,
            salience=0.051,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.archived == 1, "file below threshold must be archived"
        assert not disc.exists(), "original file must be gone after archival"

        archive_dir = tmp_path / ".archive"
        assert archive_dir.exists()
        archived_files = list(archive_dir.iterdir())
        assert len(archived_files) == 1, "exactly one file in archive"
        assert archived_files[0].name == "2026-01-01-near-archive.md"


# ------------------------------------------------------------------
# Test 6 — Idempotency: calling twice on same day must not double-decay
# ------------------------------------------------------------------

class TestIdempotency:
    def test_already_decayed_today_is_skipped(self, tmp_path: Path) -> None:
        """Running decay twice on the same calendar day must not apply decay
        a second time."""
        today = date(2026, 5, 1)
        disc = _write_discovery(
            tmp_path / "2026-01-01-idem.md",
            importance=0.3,
            salience=1.0,
            last_decay_date=today.isoformat(),
        )

        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.skipped_already_decayed == 1
        assert summary.decayed == 0
        assert _read_salience(disc) == pytest.approx(1.0, abs=1e-4), \
            "salience must be unchanged when already decayed today"


# ------------------------------------------------------------------
# Test 7 — Multi-day decay (days > 1)
# ------------------------------------------------------------------

class TestMultiDayDecay:
    def test_multi_day_decay_is_multiplicative(self, tmp_path: Path) -> None:
        """``apply_decay(days=3)`` applies (1-rate)**3 to salience."""
        disc = _write_discovery(
            tmp_path / "2026-01-01-multi.md",
            importance=0.65,  # MID → 2%/day
            salience=2.0,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, days=3, today=today)

        assert summary.decayed == 1
        expected = 2.0 * (0.98 ** 3)
        assert _read_salience(disc) == pytest.approx(expected, abs=1e-4), \
            f"3-day decay should be (1-0.02)^3 applied: expected {expected:.4f}"


# ------------------------------------------------------------------
# Test 8 — Env-overridable rates
# ------------------------------------------------------------------

class TestEnvRateOverride:
    def test_custom_high_rate_via_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEPTHFUSION_DECAY_RATE_HIGH=0.10 must be used for HIGH-bucket files."""
        monkeypatch.setenv("DEPTHFUSION_DECAY_RATE_HIGH", "0.10")
        disc = _write_discovery(
            tmp_path / "2026-01-01-env-high.md",
            importance=0.9,  # HIGH bucket
            salience=2.0,
        )
        today = date(2026, 5, 1)
        summary = apply_decay(discovery_dir=tmp_path, today=today)

        assert summary.decayed == 1
        expected = 2.0 * (1.0 - 0.10)
        assert _read_salience(disc) == pytest.approx(expected, abs=1e-4), \
            f"custom 10%/day rate not applied: expected {expected:.4f}"


# ------------------------------------------------------------------
# Test 9 — Empty / non-existent directory is handled gracefully
# ------------------------------------------------------------------

class TestEmptyDirectory:
    def test_missing_dir_returns_zero_summary(self, tmp_path: Path) -> None:
        """apply_decay on a non-existent directory returns a zeroed summary."""
        ghost_dir = tmp_path / "does-not-exist"
        summary = apply_decay(discovery_dir=ghost_dir)

        assert summary.total == 0
        assert summary.decayed == 0
        assert summary.archived == 0
        assert not summary.errors
