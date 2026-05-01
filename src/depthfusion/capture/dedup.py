"""Embedding-based discovery dedup — CM-2 / S-49 / T-149.

Problem
=======
Every capture mechanism (decision extractor, negative extractor, git
post-commit hook) writes to `~/.claude/shared/discoveries/` on its own
cadence. Over time semantically-equivalent files accumulate (same
decision captured in two sessions, same commit re-summarised after a
revert, etc.), polluting recall results and inflating the corpus.

Solution
========
Before finalising a NEW discovery, we embed its content (plus a window
of recent discoveries in the same project) and check for near-duplicate
pairs. When `cos(new, old) ≥ 0.92`, the OLDER file is superseded (renamed
to `<name>.superseded`). The newer one stays as the canonical record.

Design choices
==============
- **File-level dedup, not line-level.** Matches S-49 AC-1 exactly, and
  avoids the risk of rewriting markdown bodies mid-session.
- **Newer wins.** The newest capture is always the ground truth — it
  reflects the latest understanding after more context.
- **Project-scoped.** Only discoveries tagged with the same `project:`
  frontmatter are compared. Cross-project collisions are allowed.
- **Graceful degradation.** If the embedding backend is None or
  unavailable (no sentence-transformers), dedup is a no-op — no file is
  renamed and the caller proceeds normally.
- **Idempotent.** Running dedup twice on the same state produces no
  further changes (already-superseded files are skipped).
- **Window bounded.** We only compare against the last N discovery files
  (default 50) to keep the embedding batch small and fast.

Spec: docs/plans/v0.5/01-assessment.md §CM-2
Backlog: T-149, T-150, T-151
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DEDUP_THRESHOLD = 0.92
_DEFAULT_WINDOW_SIZE = 50
_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"
_SUPERSEDED_SUFFIX = ".superseded"

_FRONTMATTER_PROJECT_RE = re.compile(r"^project:\s*(\S+)\s*$", re.MULTILINE)

# S-70 — importance/salience frontmatter parsers.
#
# `\S+` captures single-token values (e.g. `0.83`, `five`, `not-a-number`)
# so single-token malformed inputs reach `extract_memory_score()` and are
# normalized to the canonical default. Multi-word malformed values (e.g.
# `importance: not a number`) fail the regex entirely and fall through
# the missing-field path, which also yields the canonical default — both
# routes converge on the same safe outcome.
#
# IMPORTANT: these regexes are applied to the *frontmatter block only*
# (sliced by `_extract_frontmatter_block` before the `.search` call), not
# to the full markdown body. Without that slice, a discovery whose body
# contains a line like `importance: 0.9` (e.g., a markdown bullet quoting
# the number) would silently spoof the file's score (Codex consensus
# finding, S-70 Round 1).
_FRONTMATTER_IMPORTANCE_RE = re.compile(
    r"^importance:\s*(\S+)\s*$", re.MULTILINE
)
_FRONTMATTER_SALIENCE_RE = re.compile(
    r"^salience:\s*(\S+)\s*$", re.MULTILINE
)
_FRONTMATTER_BLOCK_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL
)


def _read_threshold() -> float:
    """Dedup threshold — env-var overridable for tuning without code change."""
    raw = os.environ.get("DEPTHFUSION_DEDUP_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_DEDUP_THRESHOLD
    try:
        val = float(raw)
        return max(0.0, min(1.0, val))
    except ValueError:
        return _DEFAULT_DEDUP_THRESHOLD


def extract_project(content: str) -> str | None:
    """Pull the `project:` frontmatter value from a discovery file body."""
    m = _FRONTMATTER_PROJECT_RE.search(content)
    return m.group(1).strip() if m else None


def _extract_frontmatter_block(content: str) -> str:
    """Return the YAML frontmatter block from a discovery file body.

    A discovery starts with ``---\\n...\\n---\\n``; we slice between those
    fences and apply scoring regexes only to the inner block. Falls back
    to the empty string when no frontmatter is detected — the regex
    searches will then return no matches, and the caller fills defaults.

    Why slice instead of just running the existing ``re.MULTILINE``
    regexes against the whole document: ``importance`` and ``salience``
    are common English words. A discovery body containing a line like
    ``importance: 0.9`` (e.g. a markdown bullet quoting a value) would
    otherwise silently spoof the file's score. ``project:`` (the existing
    pattern at line 52) doesn't have this problem because it's not a
    word that appears casually in prose.
    """
    m = _FRONTMATTER_BLOCK_RE.match(content)
    return m.group(1) if m else ""


def _try_parse_float(raw: str | None) -> float | None:
    """Parse a frontmatter scalar string to a float, or None if malformed.

    Returns ``None`` for any input that ``float()`` would reject —
    non-numeric strings, empty strings, multi-word values that the regex
    captured partially. Callers must treat ``None`` as "use default."
    Never raises.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def extract_memory_score(content: str) -> "MemoryScore":  # noqa: F821 — runtime-imported below
    """Parse importance/salience scalars from discovery frontmatter (S-70).

    Returns a fully-populated ``MemoryScore``. Missing or malformed values
    fall back to canonical defaults via ``MemoryScore.__post_init__``.
    Never returns ``None``, never raises on malformed input, never bleeds
    NaN/Inf through to callers — the policy layer must be able to trust
    the return value end-to-end.

    Backward-compatible with pre-S-70 discovery files: a frontmatter
    without these fields parses to ``MemoryScore()`` (default importance
    0.5, default salience 1.0). Scoping the regex to the frontmatter
    block (not the whole document) prevents body-text spoofing.

    The ``MemoryScore`` import is lazy to keep the capture hot-path
    import cost low (matches the rationale documented at the top of
    ``_cosine``: this module runs under the git post-commit hook).
    """
    from depthfusion.core.types import MemoryScore as _MS

    block = _extract_frontmatter_block(content)
    if not block:
        return _MS()  # No frontmatter — pure defaults.

    imp_match = _FRONTMATTER_IMPORTANCE_RE.search(block)
    sal_match = _FRONTMATTER_SALIENCE_RE.search(block)

    # String → float happens here at the parse layer; MemoryScore stays
    # strictly typed as Optional[float] (no `# type: ignore` propagated
    # downstream). NaN/Inf are caught by MemoryScore.__post_init__'s
    # `math.isfinite` check.
    importance = _try_parse_float(imp_match.group(1) if imp_match else None)
    salience = _try_parse_float(sal_match.group(1) if sal_match else None)

    return _MS(importance=importance, salience=salience)


def load_discovery_corpus(
    output_dir: Path | None = None,
    *,
    window_size: int = _DEFAULT_WINDOW_SIZE,
    exclude: Path | None = None,
) -> list[tuple[Path, str, str | None]]:
    """Return the N most-recent discovery files (path, content, project).

    Skips `.superseded` files and the `exclude` path (typically the file
    currently being written, to avoid self-comparison).
    """
    out_dir = output_dir or _DISCOVERIES_DIR
    if not out_dir.exists():
        return []

    all_files = sorted(
        (p for p in out_dir.glob("*.md") if not p.name.endswith(_SUPERSEDED_SUFFIX)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if exclude is not None:
        all_files = [p for p in all_files if p != exclude]
    all_files = all_files[:window_size]

    corpus: list[tuple[Path, str, str | None]] = []
    for p in all_files:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        corpus.append((p, content, extract_project(content)))
    return corpus


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity — duplicated from retrieval.hybrid._cosine_similarity
    to avoid pulling the full retrieval module into the capture hot-path
    (capture runs under the git post-commit hook, where import weight matters).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def find_duplicates(
    new_path: Path,
    new_content: str,
    corpus: list[tuple[Path, str, str | None]],
    embeddings: list[list[float]],
    *,
    threshold: float | None = None,
) -> list[tuple[Path, float]]:
    """Return list of (existing_path, similarity) pairs that exceed threshold.

    `embeddings[0]` MUST be the embedding of `new_content`.
    `embeddings[1:]` MUST correspond 1:1 with `corpus` in order.
    """
    if threshold is None:
        threshold = _read_threshold()
    if len(embeddings) != len(corpus) + 1:
        logger.debug(
            "find_duplicates: embedding count %d does not match corpus+1 (%d); "
            "returning empty result",
            len(embeddings), len(corpus) + 1,
        )
        return []

    new_project = extract_project(new_content)
    new_vec = embeddings[0]
    dupes: list[tuple[Path, float]] = []

    for (path, _content, project), vec in zip(corpus, embeddings[1:], strict=False):
        # Strict project-scoping: only compare when BOTH sides have matching
        # project frontmatter. Files without frontmatter are never deduped
        # against anything — the conservative choice (false-negative is cheaper
        # than false-positive here, since superseding a file is near-destructive).
        if new_project is None or project is None or new_project != project:
            continue
        sim = _cosine(new_vec, vec)
        if sim >= threshold:
            dupes.append((path, sim))

    # Sort by descending similarity so caller can report the best match first
    dupes.sort(key=lambda t: -t[1])
    return dupes


def supersede(old_path: Path) -> Path | None:
    """Rename `old_path` with the `.superseded` suffix.

    Idempotent: if the `.superseded` file already exists, returns its path
    without overwriting (the newer supersession wins only on first run).
    """
    if not old_path.exists():
        return None
    target = old_path.with_name(old_path.name + _SUPERSEDED_SUFFIX)
    if target.exists():
        logger.debug("supersede: %s already exists, skipping", target.name)
        return target
    try:
        old_path.rename(target)
        logger.info("Superseded %s → %s", old_path.name, target.name)
        return target
    except OSError as exc:
        logger.warning("supersede: could not rename %s: %s", old_path, exc)
        return None


def dedup_against_corpus(
    new_path: Path,
    *,
    backend: Any = None,
    output_dir: Path | None = None,
    window_size: int = _DEFAULT_WINDOW_SIZE,
    threshold: float | None = None,
) -> list[Path]:
    """Run dedup for `new_path` against the recent discovery corpus.

    Resolves the embedding backend via `get_backend("embedding")` if not
    provided. If the backend returns None (NullBackend, missing
    sentence-transformers, encode failure), dedup is a no-op.

    Returns a list of paths that were superseded (may be empty). Never
    raises — all errors are logged at DEBUG/WARNING and downgrade gracefully.
    """
    if not new_path.exists():
        return []

    try:
        new_content = new_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("dedup_against_corpus: could not read %s: %s", new_path, exc)
        return []

    if not new_content.strip():
        return []

    corpus = load_discovery_corpus(
        output_dir=output_dir,
        window_size=window_size,
        exclude=new_path,
    )
    if not corpus:
        return []

    if backend is None:
        try:
            from depthfusion.backends.factory import get_backend
            backend = get_backend("embedding")
        except Exception as exc:  # noqa: BLE001
            logger.debug("dedup: backend resolution failed: %s", exc)
            return []

    texts = [new_content] + [c[1] for c in corpus]
    try:
        embeddings = backend.embed(texts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("dedup: embed() raised: %s", exc)
        return []

    if embeddings is None:
        # NullBackend / missing sentence-transformers — graceful no-op
        return []

    dupes = find_duplicates(
        new_path=new_path,
        new_content=new_content,
        corpus=corpus,
        embeddings=embeddings,
        threshold=threshold,
    )

    # S-60 / T-188: emit a capture event per supersede attempt. Use
    # `extract_project` on the new file so the project slug in the
    # metrics record matches the file's frontmatter (not the project
    # of the older superseded file, which might differ in edge cases).
    from depthfusion.capture._metrics import emit_capture_event
    new_project = extract_project(new_content) or "unknown"

    # Review-fix IMP-2: when dedup runs but finds no duplicates, still
    # emit an event so the metrics stream distinguishes "ran, found
    # nothing" (the common case) from "never ran". Without this, a dedup
    # pass against a clean corpus would leave no audit trail.
    if not dupes:
        emit_capture_event(
            capture_mechanism="dedup",
            project=new_project,
            session_id=new_path.stem,
            write_success=True,      # dedup completed successfully
            entries_written=0,       # but zero files superseded
            file_path=str(new_path),
        )
        return []

    superseded: list[Path] = []
    for old_path, sim in dupes:
        logger.info(
            "Dedup: %s supersedes %s (cos-sim %.3f)",
            new_path.name, old_path.name, sim,
        )
        result = supersede(old_path)
        success = result is not None
        emit_capture_event(
            capture_mechanism="dedup",
            project=new_project,
            session_id=new_path.stem,
            write_success=success,
            entries_written=1 if success else 0,
            file_path=str(result) if result else str(old_path),
        )
        if success:
            superseded.append(old_path)
    return superseded


__all__ = [
    "dedup_against_corpus",
    "extract_memory_score",
    "extract_project",
    "find_duplicates",
    "load_discovery_corpus",
    "supersede",
]
