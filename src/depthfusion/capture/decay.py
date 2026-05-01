"""Bucketed salience decay for discovery files — S-71.

Applies daily salience decay to all discovery files in a given directory,
bucketing the decay rate by ``importance``:

    pinned (``pinned: true``)    →  0%/day  (skip entirely)
    importance >= 0.8            →  1%/day  (HIGH bucket)
    importance >= 0.5            →  2%/day  (MID bucket)
    importance < 0.5             →  5%/day  (LOW bucket)

Decay is multiplicative:
    new_salience = salience * (1 - rate) ** days

Files whose salience drops below the hard-archive threshold (default 0.05)
are moved to a ``.archive/`` sub-directory of the discovery store instead of
being modified further.

Idempotency
-----------
The date of the most recent decay pass is written as ``last_decay_date:
YYYY-MM-DD`` into each file's frontmatter. If ``apply_decay`` is called
multiple times on the same calendar day with the same ``days`` argument, the
file is skipped — double-decay cannot occur.

All frontmatter writes go through ``atomic_frontmatter_rewrite`` from
``core.file_locking`` so concurrent access (e.g. a simultaneous
``set_memory_score`` call) is safe.

Env-configurable rates (read at call time, not module import time):
    DEPTHFUSION_DECAY_RATE_HIGH          (default 0.01)
    DEPTHFUSION_DECAY_RATE_MID           (default 0.02)
    DEPTHFUSION_DECAY_RATE_LOW           (default 0.05)
    DEPTHFUSION_HARD_ARCHIVE_THRESHOLD   (default 0.05)
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Constants / regexes (applied to frontmatter block only)
# ------------------------------------------------------------------

_FRONTMATTER_BLOCK_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL
)
_PINNED_RE = re.compile(r"^pinned:\s*(.+?)\s*$", re.MULTILINE)
_LAST_DECAY_DATE_RE = re.compile(r"^last_decay_date:\s*(\S+)\s*$", re.MULTILINE)

_SALIENCE_MIN = 0.0
_SALIENCE_MAX = 5.0


# ------------------------------------------------------------------
# Public dataclass
# ------------------------------------------------------------------

@dataclass
class DecaySummary:
    """Summary of a single ``apply_decay`` run.

    Attributes:
        total:    Number of ``.md`` files found in the directory.
        skipped_pinned:  Files with ``pinned: true`` — not touched.
        skipped_already_decayed: Files already decayed today.
        decayed:  Files whose salience was reduced.
        archived: Files moved to ``.archive/`` (salience < threshold).
        errors:   Per-file errors encountered (file path → error message).
    """
    total: int = 0
    skipped_pinned: int = 0
    skipped_already_decayed: int = 0
    decayed: int = 0
    archived: int = 0
    errors: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _extract_frontmatter_block(body: str) -> str:
    """Return the inner YAML frontmatter string (between the ``---`` fences)."""
    m = _FRONTMATTER_BLOCK_RE.match(body)
    return m.group(1) if m else ""


def _is_pinned(fm_block: str) -> bool:
    """Return True iff the frontmatter contains ``pinned: true`` (case-insensitive)."""
    m = _PINNED_RE.search(fm_block)
    if not m:
        return False
    return m.group(1).strip().lower() in {"true", "1", "yes"}


def _get_last_decay_date(fm_block: str) -> Optional[date]:
    """Parse ``last_decay_date`` from frontmatter; return ``None`` if absent/malformed."""
    m = _LAST_DECAY_DATE_RE.search(fm_block)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1).strip())
    except (ValueError, AttributeError):
        return None


def _bucket_rate(importance: float, rate_high: float, rate_mid: float, rate_low: float) -> float:
    """Return the per-day decay rate for the given importance value."""
    if importance >= 0.8:
        return rate_high
    if importance >= 0.5:
        return rate_mid
    return rate_low


def _default_discoveries_dir() -> Path:
    return Path.home() / ".claude" / "shared" / "discoveries"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def apply_decay(
    discovery_dir: Optional[Path] = None,
    *,
    days: int = 1,
    today: Optional[date] = None,
) -> DecaySummary:
    """Apply bucketed salience decay to all discovery files in *discovery_dir*.

    Args:
        discovery_dir: Path to the directory containing ``.md`` discovery
            files. Defaults to ``~/.claude/shared/discoveries/``.
        days: Number of days of decay to apply (typically 1 for a daily cron).
            The idempotency check compares ``last_decay_date`` against *today*;
            if they match, the file is skipped regardless of *days*.
        today: Override today's date (for testing). Defaults to
            ``date.today()`` in the local timezone.

    Returns:
        A :class:`DecaySummary` instance describing what was done.

    Reads env vars for rates at call time (not module import time) so tests
    can ``monkeypatch.setenv`` without reloading the module.
    """
    from depthfusion.capture.dedup import extract_memory_score
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.core.file_locking import atomic_frontmatter_rewrite

    cfg = DepthFusionConfig.from_env()
    rate_high = cfg.decay_rate_high
    rate_mid = cfg.decay_rate_mid
    rate_low = cfg.decay_rate_low
    archive_threshold = cfg.hard_archive_threshold

    out_dir = discovery_dir or _default_discoveries_dir()
    run_date = today or date.today()
    summary = DecaySummary()

    if not out_dir.exists():
        return summary

    try:
        files = sorted(f for f in out_dir.iterdir() if f.is_file() and f.suffix == ".md" and not f.name.startswith("."))
    except OSError as exc:
        logger.debug("apply_decay: could not list %s: %s", out_dir, exc)
        return summary

    summary.total = len(files)

    for path in files:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            summary.errors[str(path)] = f"read error: {exc}"
            continue

        fm_block = _extract_frontmatter_block(body)

        # --- pinned: skip entirely ---
        if _is_pinned(fm_block):
            summary.skipped_pinned += 1
            continue

        # --- idempotency: skip if already decayed today ---
        last_decay = _get_last_decay_date(fm_block)
        if last_decay is not None and last_decay >= run_date:
            summary.skipped_already_decayed += 1
            continue

        # --- compute new salience ---
        score = extract_memory_score(body)
        rate = _bucket_rate(score.importance, rate_high, rate_mid, rate_low)
        new_salience = score.salience * (1.0 - rate) ** days
        new_salience = max(_SALIENCE_MIN, min(_SALIENCE_MAX, new_salience))

        # --- hard-archive if below threshold ---
        if new_salience < archive_threshold:
            archived_path = _archive_file(path, out_dir)
            if archived_path is not None:
                summary.archived += 1
                logger.info(
                    "Archived (salience %.4f < %.4f): %s -> %s",
                    new_salience, archive_threshold, path.name, archived_path.name,
                )
            else:
                summary.errors[str(path)] = "archive move failed"
            continue

        # --- write decayed salience + last_decay_date ---
        try:
            with atomic_frontmatter_rewrite(path) as ctx:
                ctx.set_score(
                    salience=new_salience,
                    last_decay_date=run_date.isoformat(),
                )
            summary.decayed += 1
            logger.debug(
                "Decayed %s: importance=%.4f rate=%.3f "
                "salience %.4f → %.4f",
                path.name, score.importance, rate, score.salience, new_salience,
            )
        except Exception as exc:
            summary.errors[str(path)] = f"write error: {exc}"

    return summary


def _archive_file(path: Path, discovery_dir: Path) -> Optional[Path]:
    """Move *path* into ``discovery_dir/.archive/``; return the destination or None."""
    archive_dir = discovery_dir / ".archive"
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("apply_decay: cannot create archive dir %s: %s", archive_dir, exc)
        return None

    dest = archive_dir / path.name
    if dest.exists():
        # Collision — append datestamp to stem
        stem, suffix = path.stem, path.suffix
        ts = date.today().strftime("%Y%m%d")
        dest = archive_dir / f"{stem}.{ts}{suffix}"

    try:
        shutil.move(str(path), str(dest))
        return dest
    except OSError as exc:
        logger.debug("apply_decay: cannot move %s to archive: %s", path, exc)
        return None


__all__ = ["apply_decay", "DecaySummary"]
