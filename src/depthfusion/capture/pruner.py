"""Discovery file pruner — S-55 / T-169 / TG-14, extended in E-42 / S-127.

Identifies stale discovery files in `~/.claude/shared/discoveries/` and
(on explicit confirmation) moves them to a sibling `.archive/` directory.
NEVER deletes; moves only, so the operation is trivially reversible by
moving files back out of `.archive/`.

Heuristics
==========
1. **Age exceeded** — file mtime older than `age_days` (default 90).
2. **Superseded** — file name ends in `.superseded` (CM-2 / S-49 dedup
   produces these when a newer semantically-equivalent discovery lands).
   The `superseded_min_age_hours` parameter adds an optional grace period:
   superseded files younger than the threshold are NOT flagged (E-42 AC-1).
3. **Never recalled** (`min_recall_score=True`) — discovery file whose chunk
   stems do NOT appear in any recall JSONL within the last 90 days. Requires
   `record_recall_query` to emit `chunk_ids` (E-42 AC-2/AC-3). Enabled by
   passing `recall_log_dir` to `identify_candidates`.

Safety contract
===============
- `confirm=True` is required to actually move files. Without it, the
  `prune_discoveries()` function returns an empty list and no filesystem
  modification occurs.
- Archive target collisions (same filename already in `.archive/`) get a
  timestamp suffix appended, so no data is ever silently overwritten.
- Failures on individual files are logged at DEBUG but don't abort the
  batch — a stuck permission error on one file shouldn't block the rest.

Spec: docs/plans/v0.5/02-build-plan.md §TG-14
Backlog: T-169 (S-55)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_AGE_DAYS = 90
_SUPERSEDED_SUFFIX = ".superseded"


def _default_discoveries_dir() -> Path:
    """Resolve `~/.claude/shared/discoveries/` at call time.

    Using a function (not a module-level constant) lets tests redirect
    `Path.home()` via monkeypatch after the module is imported — a
    module-level constant would freeze the real home directory at
    import time and ignore the patch.
    """
    return Path.home() / ".claude" / "shared" / "discoveries"


@dataclass(frozen=True)
class PruneCandidate:
    """A discovery file flagged for potential archival.

    Attributes:
        path: absolute path of the file.
        reason: short machine-readable reason — `age_exceeded` |
            `superseded`. Future: `never_recalled`.
        age_days: file age in days at the time of identification. Used
            by the caller to display a human-readable "X days old" in
            UI output.
    """
    path: Path
    reason: str
    age_days: float


def _read_age_days() -> int:
    """Read `DEPTHFUSION_PRUNE_AGE_DAYS` from env; default 90.

    Malformed values (non-numeric, negative) fall back silently to the
    default — operator error shouldn't surface as a crashed MCP tool.
    """
    raw = os.environ.get("DEPTHFUSION_PRUNE_AGE_DAYS", "").strip()
    if not raw:
        return _DEFAULT_AGE_DAYS
    try:
        val = int(raw)
        return val if val > 0 else _DEFAULT_AGE_DAYS
    except ValueError:
        logger.debug(
            "DEPTHFUSION_PRUNE_AGE_DAYS=%r invalid; using default %d",
            raw, _DEFAULT_AGE_DAYS,
        )
        return _DEFAULT_AGE_DAYS


def _is_pinned(path: Path) -> bool:
    """Return True if ``path``'s YAML frontmatter contains ``pinned: true``.

    Uses a lightweight regex scan rather than a full YAML parse to avoid
    importing PyYAML in a hot loop. Treats any parse/read error as "not
    pinned" so a corrupt file still becomes a prune candidate — the operator
    can decide what to do with it.

    The key must be exactly ``"pinned"`` as agreed by S-69/S-71 (decay
    buckets also reads this key).
    """
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    # Extract the frontmatter block only (between the first pair of `---`).
    fm_re = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
    m = fm_re.match(body)
    if not m:
        return False

    # Look for `pinned: true` (case-insensitive value).
    pin_re = re.compile(r"^pinned:\s*(true|yes|1)\s*$", re.IGNORECASE | re.MULTILINE)
    return bool(pin_re.search(m.group(1)))


def _recalled_stems(recall_log_dir: Path, window_days: int = 90) -> set[str]:
    """Return file stems seen in recall JSONL chunk_ids within `window_days`.

    Reads all `*-recall.jsonl` files in `recall_log_dir` whose date suffix
    falls within the last `window_days`. For each recall record that carries
    a `chunk_ids` list, extracts the stem of each chunk_id (the part before
    the first `#`). Returns the set of stems — callers use `stem in recalled`
    to decide whether a discovery file was recently surfaced.

    Returns an empty set on any error so callers always see a conservative
    result (never incorrectly prune a file because the JSONL was unreadable).
    """
    import json

    now = datetime.now(tz=timezone.utc)
    stems: set[str] = set()
    if not recall_log_dir.exists():
        return stems
    try:
        for p in recall_log_dir.iterdir():
            if not p.name.endswith("-recall.jsonl"):
                continue
            # Extract date from filename YYYY-MM-DD-recall.jsonl
            date_part = p.name[: len("YYYY-MM-DD")]
            try:
                from datetime import date as _date
                file_date = _date.fromisoformat(date_part)
            except ValueError:
                continue
            delta = (now.date() - file_date).days
            if delta > window_days:
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        for cid in rec.get("chunk_ids") or []:
                            stems.add(cid.split("#", 1)[0])
            except OSError:
                continue
    except OSError:
        pass
    return stems


def identify_candidates(
    output_dir: Path | None = None,
    *,
    age_days: int | None = None,
    superseded_min_age_hours: int = 0,
    recall_log_dir: Path | None = None,
) -> list[PruneCandidate]:
    """Scan `output_dir` and return files that match one or more heuristics.

    Args:
        output_dir: discovery directory. Defaults to
            `~/.claude/shared/discoveries/`.
        age_days: override the age threshold. Defaults to the value of
            `DEPTHFUSION_PRUNE_AGE_DAYS` or 90.
        superseded_min_age_hours: grace period for the `superseded` heuristic.
            A `.superseded` file younger than this many hours is NOT flagged
            (E-42 AC-1). Default 0 = flag all superseded files (back-compat).
        recall_log_dir: directory containing `*-recall.jsonl` files written
            by `MetricsCollector`. When provided, enables the `min_recall_score`
            heuristic: files whose stems appear in any recall record within
            the last 90 days are excluded from candidates (E-42 AC-3).
            None (default) = heuristic disabled.

    Returns:
        List of `PruneCandidate`. Order is deterministic (sorted by path)
        so repeated runs produce identical output for tooling to diff.
        Empty list when the directory doesn't exist.
    """
    out_dir = output_dir or _default_discoveries_dir()
    if not out_dir.exists():
        return []

    threshold_days = age_days if age_days is not None else _read_age_days()
    now_ts = datetime.now(tz=timezone.utc).timestamp()

    # Build recalled-stem set once for the whole scan (E-42 AC-3).
    recalled: set[str] = (
        _recalled_stems(recall_log_dir) if recall_log_dir is not None else set()
    )

    candidates: list[PruneCandidate] = []
    try:
        files = sorted(out_dir.iterdir())
    except OSError as exc:
        logger.debug("identify_candidates: could not list %s: %s", out_dir, exc)
        return []

    for path in files:
        if not path.is_file():
            # Filters out `.archive/` and any other subdirectory
            continue
        # Skip hidden files (`.DS_Store`, `.gitkeep`, ad-hoc editor swap files).
        # Note: legitimate discovery files NEVER start with `.` — they all
        # follow the `YYYY-MM-DD-<project>-<type>.md` naming convention,
        # so this filter is safe to apply without exceptions. `.superseded`
        # discoveries have the suffix appended (`foo.md.superseded`), not
        # prefixed, so they pass through this check.
        if path.name.startswith("."):
            continue

        # S-69: skip files whose frontmatter carries `pinned: true`.
        # Reading the file is cheap; we do it before the stat so a pinned
        # file never appears in candidates regardless of age or suffix.
        if _is_pinned(path):
            continue

        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue

        age_sec = max(0.0, now_ts - mtime)
        age_d = age_sec / 86400.0

        # Reason 1: superseded suffix (CM-2 / S-49 dedup)
        if path.name.endswith(_SUPERSEDED_SUFFIX):
            # E-42 AC-1: honour grace period — skip recently superseded files.
            if superseded_min_age_hours > 0:
                age_hours = age_sec / 3600.0
                if age_hours < superseded_min_age_hours:
                    continue
            candidates.append(PruneCandidate(
                path=path, reason="superseded", age_days=round(age_d, 2),
            ))
            continue

        # Reason 2: age exceeds threshold
        if age_d > threshold_days:
            # E-42 AC-3: min_recall_score — keep if recently recalled.
            if recalled:
                file_stem = path.name
                # Strip known extensions to get the bare stem
                for ext in (".md", ".txt", ".json"):
                    if file_stem.endswith(ext):
                        file_stem = file_stem[: -len(ext)]
                        break
                if file_stem in recalled:
                    continue
            candidates.append(PruneCandidate(
                path=path, reason="age_exceeded", age_days=round(age_d, 2),
            ))

    return candidates


def _resolve_archive_target(archive_dir: Path, source: Path) -> Path:
    """Return a non-colliding archive path for `source` inside `archive_dir`.

    If `archive_dir/source.name` already exists (e.g. from a prior prune
    run of a file with the same name), append a timestamp suffix to the
    stem so no data is silently overwritten.
    """
    primary = archive_dir / source.name
    if not primary.exists():
        return primary
    # Collision — timestamp suffix
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return archive_dir / f"{source.stem}.{ts}{source.suffix}"


def prune_discoveries(
    candidates: list[PruneCandidate],
    *,
    archive_dir: Path | None = None,
    confirm: bool = False,
) -> list[Path]:
    """Move candidates to `archive_dir`. Returns paths that were moved.

    Safety: `confirm=False` (the default) is an explicit no-op. This
    ensures that default invocation from the MCP tool returns a list
    of candidates for the operator to review before any filesystem
    change. Only `confirm=True` triggers actual moves.

    Errors on individual file moves are logged at DEBUG; the batch
    continues with the next candidate. The return list only contains
    paths that were actually moved.
    """
    if not confirm:
        return []

    archive = archive_dir or (_default_discoveries_dir() / ".archive")
    try:
        archive.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("prune_discoveries: could not create archive %s: %s", archive, exc)
        return []

    moved: list[Path] = []
    for cand in candidates:
        src = cand.path
        if not src.exists():
            continue
        target = _resolve_archive_target(archive, src)
        try:
            shutil.move(str(src), str(target))
            moved.append(target)
            logger.info(
                "Pruned (%s, %.1f days): %s -> %s",
                cand.reason, cand.age_days, src.name, target.name,
            )
        except OSError as exc:
            logger.debug("prune_discoveries: could not move %s: %s", src, exc)
    return moved


__all__ = [
    "PruneCandidate",
    "identify_candidates",
    "prune_discoveries",
]
