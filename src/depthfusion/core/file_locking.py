"""Atomic frontmatter rewrite helper — extracted from S-70 for reuse (S-72).

Single public symbol: ``atomic_frontmatter_rewrite(path)`` — context manager
that holds an exclusive ``fcntl`` lock on a sidecar ``.scorelock`` file,
yields a mutable ``FrontmatterContext``, and on exit splices the new
importance/salience scalars into the YAML frontmatter, writes via
``mkstemp`` + ``os.replace`` for torn-write safety.

Used by ``_tool_set_memory_score`` (S-70) and ``RecallStore.apply_feedback``
(S-72). Will also be used by S-71 (decay) and S-69 (pin) when they land.
"""
from __future__ import annotations

import fcntl
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional


def _splice_memory_score_frontmatter(
    body: str, importance: float, salience: float,
) -> str:
    """Return ``body`` with importance/salience set in the frontmatter block.

    - Existing ``importance:`` / ``salience:`` lines are rewritten in place.
    - Missing lines are appended just before the closing ``---``.
    - If the body has no frontmatter block, one is created at the top.
    - Preserves all other frontmatter fields and body content verbatim.

    Moved here from ``mcp.server`` (S-72) so ``atomic_frontmatter_rewrite``
    has no dependency on the MCP layer.
    """
    imp_line = f"importance: {importance:.4f}"
    sal_line = f"salience: {salience:.4f}"

    fm_re = re.compile(r"\A(---\s*\n)(.*?)(\n---\s*(?:\n|\Z))", re.DOTALL)
    m = fm_re.match(body)
    if not m:
        synthesized = (
            "---\n"
            f"{imp_line}\n"
            f"{sal_line}\n"
            "---\n"
        )
        return synthesized + body

    open_fence, fm_body, close_fence = m.group(1), m.group(2), m.group(3)

    fm_body = re.sub(
        r"^importance:.*?\r?$", "", fm_body, count=0, flags=re.MULTILINE,
    )
    fm_body = re.sub(
        r"^salience:.*?\r?$", "", fm_body, count=0, flags=re.MULTILINE,
    )
    fm_body = re.sub(r"\n{2,}", "\n", fm_body).strip("\n")
    fm_body = fm_body + "\n" + imp_line + "\n" + sal_line

    return open_fence + fm_body + close_fence + body[m.end():]


@dataclass
class FrontmatterContext:
    """Mutable container yielded by atomic_frontmatter_rewrite.

    The caller calls ``set_score`` to declare the new importance/salience
    values; the context manager applies them on exit. ``body`` exposes the
    file's current contents for callers that need to read existing values.
    """
    body: str
    _importance: Optional[float] = field(default=None)
    _salience: Optional[float] = field(default=None)
    _dirty: bool = field(default=False)

    def set_score(
        self,
        importance: Optional[float] = None,
        salience: Optional[float] = None,
    ) -> None:
        """Declare new score values to splice on context exit.

        Pass ``None`` for any field to leave it unchanged. Calling multiple
        times within the same context replaces the previous declaration.
        """
        if importance is not None:
            self._importance = importance
        if salience is not None:
            self._salience = salience
        self._dirty = True


@contextmanager
def atomic_frontmatter_rewrite(path: Path) -> Iterator[FrontmatterContext]:
    """Lock-serialized RMW on a discovery file's scoring frontmatter.

    Acquires ``fcntl.LOCK_EX`` on a sidecar ``.<filename>.scorelock`` file,
    yields a ``FrontmatterContext`` with the file's body, and on exit (if
    the caller invoked ``set_score``) splices in the new importance/salience
    via the existing ``_splice_memory_score_frontmatter`` helper, writes to
    a unique ``mkstemp`` sibling, fsyncs, then ``os.replace`` over the
    target. ``os.replace`` is atomic on POSIX — process kill mid-write
    leaves the previous file intact.

    Sidecar lock (not the target itself) so ``os.replace``'s inode swap
    doesn't invalidate the lock for concurrent waiters.
    """
    if not path.exists():
        raise FileNotFoundError(f"target does not exist: {path}")

    lock_path = path.parent / f".{path.name}.scorelock"
    lock_fh = open(lock_path, "a", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            body = path.read_text(encoding="utf-8")
            ctx = FrontmatterContext(body=body)
            yield ctx
            if not ctx._dirty:
                return

            # Resolve final values: parse existing for any unsupplied side.
            from depthfusion.capture.dedup import extract_memory_score
            from depthfusion.core.types import MemoryScore
            existing = extract_memory_score(body)
            final_imp = (
                existing.importance if ctx._importance is None else ctx._importance
            )
            final_sal = (
                existing.salience if ctx._salience is None else ctx._salience
            )
            normalized = MemoryScore(importance=final_imp, salience=final_sal)

            new_body = _splice_memory_score_frontmatter(
                body, normalized.importance, normalized.salience,
            )

            fd, tmp_str = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tf:
                    tf.write(new_body)
                    tf.flush()
                    os.fsync(tf.fileno())
                os.replace(tmp_str, str(path))
            except Exception:
                try:
                    os.unlink(tmp_str)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    finally:
        lock_fh.close()
