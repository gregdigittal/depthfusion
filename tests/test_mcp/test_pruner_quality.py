# tests/test_mcp/test_pruner_quality.py
"""E-42 / S-127 — Pruner quality improvement tests.

AC-4: ≥ 5 new tests covering:
  - superseded_min_age_hours grace period (AC-1)
  - chunk_ids captured in record_recall_query (AC-2)
  - min_recall_score inclusion: file IS recalled → not a candidate (AC-3)
  - min_recall_score exclusion: file NOT recalled → still a candidate (AC-3)
  - back-compat: old recall JSONL without chunk_ids field doesn't crash (AC-4)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from depthfusion.capture.pruner import identify_candidates

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(
    parent: Path,
    name: str,
    content: str = "---\nproject: test\n---\n\nbody\n",
    *,
    age_days: float = 0.0,
) -> Path:
    p = parent / name
    p.write_text(content, encoding="utf-8")
    if age_days:
        mtime = time.time() - age_days * 86400.0
        os.utime(p, (mtime, mtime))
    return p


def _make_recall_jsonl(metrics_dir: Path, *, chunk_ids: list[str]) -> Path:
    """Write a single recall JSONL record with the given chunk_ids."""
    from datetime import date

    metrics_dir.mkdir(parents=True, exist_ok=True)
    p = metrics_dir / f"{date.today().isoformat()}-recall.jsonl"
    record = {
        "event": "recall_query",
        "event_subtype": "ok",
        "query_hash": "testhash",
        "mode": "local",
        "backend_used": {},
        "backend_fallback_chain": {},
        "latency_ms_per_capability": {},
        "total_latency_ms": 10.0,
        "result_count": len(chunk_ids),
        "config_version_id": "none",
        "chunk_ids": chunk_ids,
    }
    p.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# AC-1: superseded_min_age_hours grace period
# ---------------------------------------------------------------------------

class TestSupersededGracePeriod:
    def test_young_superseded_file_skipped_within_grace(
        self, tmp_path: Path
    ) -> None:
        """A superseded file younger than superseded_min_age_hours is NOT a candidate."""
        _make_file(tmp_path, "recent.md.superseded", age_days=0.01)  # ~15 minutes old
        candidates = identify_candidates(
            tmp_path, superseded_min_age_hours=1
        )
        names = [c.path.name for c in candidates]
        assert "recent.md.superseded" not in names

    def test_old_superseded_file_flagged_after_grace(
        self, tmp_path: Path
    ) -> None:
        """A superseded file older than grace period IS a candidate."""
        _make_file(tmp_path, "old.md.superseded", age_days=2.0)  # 48h old
        candidates = identify_candidates(
            tmp_path, superseded_min_age_hours=1  # 1h grace — well past
        )
        names = [c.path.name for c in candidates]
        assert "old.md.superseded" in names

    def test_default_zero_grace_all_superseded_flagged(
        self, tmp_path: Path
    ) -> None:
        """Default superseded_min_age_hours=0 preserves old behaviour — all flagged."""
        _make_file(tmp_path, "brand-new.md.superseded", age_days=0.001)
        candidates = identify_candidates(tmp_path)
        assert any(c.path.name == "brand-new.md.superseded" for c in candidates)


# ---------------------------------------------------------------------------
# AC-3: min_recall_score heuristic
# ---------------------------------------------------------------------------

class TestMinRecallScoreHeuristic:
    def test_recalled_file_excluded_from_candidates(
        self, tmp_path: Path
    ) -> None:
        """A stale file whose stem was seen in recall is NOT a prune candidate."""
        disc_dir = tmp_path / "discoveries"
        disc_dir.mkdir()
        metrics_dir = tmp_path / "metrics"

        # Old file: would be age_exceeded
        _make_file(disc_dir, "2026-01-01-depthfusion-decision.md", age_days=200.0)

        # Write recall JSONL referencing its stem
        _make_recall_jsonl(
            metrics_dir,
            chunk_ids=["2026-01-01-depthfusion-decision", "other-stem#1"],
        )

        candidates = identify_candidates(
            disc_dir, age_days=90, recall_log_dir=metrics_dir
        )
        names = [c.path.name for c in candidates]
        assert "2026-01-01-depthfusion-decision.md" not in names

    def test_unrecalled_file_remains_candidate(
        self, tmp_path: Path
    ) -> None:
        """A stale file NOT in any recall stays a prune candidate."""
        disc_dir = tmp_path / "discoveries"
        disc_dir.mkdir()
        metrics_dir = tmp_path / "metrics"

        _make_file(disc_dir, "2025-01-01-old-project-notes.md", age_days=200.0)
        _make_recall_jsonl(
            metrics_dir,
            chunk_ids=["some-other-stem"],  # different stem
        )

        candidates = identify_candidates(
            disc_dir, age_days=90, recall_log_dir=metrics_dir
        )
        names = [c.path.name for c in candidates]
        assert "2025-01-01-old-project-notes.md" in names

    def test_old_recall_jsonl_without_chunk_ids_does_not_crash(
        self, tmp_path: Path
    ) -> None:
        """JSONL records written before E-42 (no chunk_ids key) must not raise."""
        disc_dir = tmp_path / "discoveries"
        disc_dir.mkdir()
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        _make_file(disc_dir, "2025-06-01-legacy-note.md", age_days=200.0)

        # Write a recall record WITHOUT chunk_ids (old format)
        from datetime import date
        p = metrics_dir / f"{date.today().isoformat()}-recall.jsonl"
        old_record = {
            "event": "recall_query",
            "event_subtype": "ok",
            "query_hash": "xyz",
            "mode": "local",
            "backend_used": {},
            "backend_fallback_chain": {},
            "latency_ms_per_capability": {},
            "total_latency_ms": 5.0,
            "result_count": 0,
            "config_version_id": "none",
            # No chunk_ids key — old format
        }
        p.write_text(json.dumps(old_record) + "\n", encoding="utf-8")

        # Must not raise; old record contributes nothing to recalled set
        candidates = identify_candidates(
            disc_dir, age_days=90, recall_log_dir=metrics_dir
        )
        names = [c.path.name for c in candidates]
        assert "2025-06-01-legacy-note.md" in names

    def test_no_recall_log_dir_disables_heuristic(
        self, tmp_path: Path
    ) -> None:
        """When recall_log_dir=None, the heuristic is off; age_exceeded still fires."""
        disc_dir = tmp_path / "discoveries"
        disc_dir.mkdir()
        _make_file(disc_dir, "2025-03-01-stale.md", age_days=200.0)

        candidates = identify_candidates(disc_dir, age_days=90, recall_log_dir=None)
        names = [c.path.name for c in candidates]
        assert "2025-03-01-stale.md" in names
