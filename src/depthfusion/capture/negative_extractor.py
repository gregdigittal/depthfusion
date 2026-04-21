"""negative_extractor.py — Negative-signal capture (CM-6).

Extracts "X did not work because Y" patterns from session transcripts and
writes them as discovery files tagged with `type: negative`. Negative signals
are used for future downweighting in retrieval.

CM-6 contract (S-48):
  - Entries written with `type: negative` frontmatter
  - False-negative rate ≤ 10% on labelled set
  - Falls back to regex heuristic when LLM backend unavailable

Spec: docs/plans/v0.5/01-assessment.md §CM-6
Backlog: T-146, T-147, T-148
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"

# Heuristic patterns that signal a failure or something that didn't work.
# Ordered by specificity — more specific patterns first.
_NEGATIVE_PATTERNS = [
    # "X did not work because Y"
    (r"(.{10,150})\s+did(?:n'?t| not)\s+work\s+because\s+(.{5,200})", re.IGNORECASE),
    # "X failed with Y"
    (r"(.{10,100})\s+failed\s+(?:with|because|due to)\s+(.{5,200})", re.IGNORECASE),
    # "X doesn't support Y"
    (r"(.{5,80})\s+doesn'?t\s+support\s+(.{5,100})", re.IGNORECASE),
    # "X is incompatible with Y"
    (r"(.{5,80})\s+is(?:n'?t)?\s+incompatible\s+with\s+(.{5,100})", re.IGNORECASE),
    # "DO NOT / NEVER X"
    (r"^(?:DO NOT|NEVER|DON'T)\s+(.{10,200})$", re.MULTILINE | re.IGNORECASE),
    # "X causes Y"
    (r"(.{10,100})\s+causes?\s+(.{5,100}(?:error|bug|issue|crash|failure))", re.IGNORECASE),
    # "avoid X" or "don't use X"
    (r"^(?:avoid|don'?t use|never use)\s+(.{10,200})$", re.MULTILINE | re.IGNORECASE),
    # Error/exception mentions with context
    (r"(?:error|exception|traceback):\s*(.{10,200})", re.IGNORECASE),
]

_DEFAULT_CONFIDENCE = 0.70

_NEGATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "negatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}

_NEGATIVE_PROMPT = """\
From this session transcript, extract patterns where something didn't work, \
failed, caused problems, or should be avoided. For each:
  what: what failed or should be avoided (max 100 chars)
  why: reason or consequence (max 150 chars)
  confidence: 0.0-1.0 (how clearly this was a confirmed failure vs speculation)

Extract at most 10 items. Only include genuine failures, not tentative concerns.

Session transcript:
{content}"""


class NegativeEntry:
    """A single negative-signal entry."""

    __slots__ = ("what", "why", "confidence", "source_session")

    def __init__(
        self,
        what: str,
        why: str,
        confidence: float = _DEFAULT_CONFIDENCE,
        source_session: str = "",
    ) -> None:
        self.what = what
        self.why = why
        self.confidence = max(0.0, min(1.0, confidence))
        self.source_session = source_session

    def __repr__(self) -> str:  # pragma: no cover
        return f"NegativeEntry({self.what[:40]!r})"


class HeuristicNegativeExtractor:
    """Regex-based fallback extractor for negative signals."""

    def extract(self, content: str, source_session: str = "") -> list[NegativeEntry]:
        if not content or len(content.strip()) < 20:
            return []
        results: list[NegativeEntry] = []
        seen: set[str] = set()
        for pattern, flags in _NEGATIVE_PATTERNS:
            for m in re.finditer(pattern, content, flags):
                groups = m.groups()
                if len(groups) >= 2:
                    what = groups[0].strip()
                    why = groups[1].strip()
                else:
                    what = groups[0].strip()
                    why = ""
                if len(what) < 5 or what in seen:
                    continue
                seen.add(what)
                results.append(NegativeEntry(
                    what=what[:200],
                    why=why[:200],
                    confidence=_DEFAULT_CONFIDENCE,
                    source_session=source_session,
                ))
                if len(results) >= 30:
                    break
        return results[:30]


class LLMNegativeExtractor:
    """LLM-backed negative signal extractor.

    Uses the `decision_extractor` backend capability (negative signals are a
    subset of decision extraction). Falls back to HeuristicNegativeExtractor
    when the backend is unavailable.
    """

    def __init__(self, backend: Any = None) -> None:
        self._backend: Any = None

        if backend is not None:
            self._backend = backend
            return

        flag = os.environ.get("DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", "false").strip().lower()
        if flag not in ("true", "1", "yes"):
            return

        from depthfusion.backends.factory import get_backend
        resolved = get_backend("decision_extractor")
        if resolved.healthy():
            self._backend = resolved

    def is_available(self) -> bool:
        return self._backend is not None and self._backend.healthy()

    def extract(self, content: str, source_session: str = "") -> list[NegativeEntry]:
        if not self.is_available():
            return HeuristicNegativeExtractor().extract(content, source_session)

        try:
            result = self._backend.extract_structured(
                _NEGATIVE_PROMPT.format(content=content[:4000]),
                schema=_NEGATIVE_SCHEMA,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM negative extractor failed (%s), falling back to heuristic", exc)
            return HeuristicNegativeExtractor().extract(content, source_session)

        if not result or not isinstance(result.get("negatives"), list):
            return HeuristicNegativeExtractor().extract(content, source_session)

        entries: list[NegativeEntry] = []
        seen: set[str] = set()
        for item in result["negatives"]:
            if not isinstance(item, dict):
                continue
            what = str(item.get("what", "")).strip()
            if len(what) < 5 or what in seen:
                continue
            seen.add(what)
            entries.append(NegativeEntry(
                what=what[:200],
                why=str(item.get("why", "")).strip()[:200],
                confidence=max(0.0, min(1.0, float(item.get("confidence", 0.7)))),
                source_session=source_session,
            ))
            if len(entries) >= 10:
                break

        return entries if entries else HeuristicNegativeExtractor().extract(
            content, source_session
        )


def write_negatives(
    entries: list[NegativeEntry],
    project: str,
    session_id: str,
    output_dir: Path | None = None,
) -> Path | None:
    """Write negative entries to a discovery file with `type: negative` frontmatter.

    Idempotent: if the output file already exists, does not overwrite.

    Returns path on success, None if nothing to write or already exists.

    Note on metrics (S-60): this function hard-codes
    `capture_mechanism="negative_extractor"` in its emit calls. There is
    no override parameter (unlike `decision_extractor.write_decisions`)
    because no higher-level tool currently wraps this function the way
    `_tool_confirm_discovery` wraps `write_decisions`. If a future MCP
    tool wants to re-bucket negative-extractor events under its own
    mechanism name, add a `capture_mechanism` parameter following the
    decision_extractor pattern.
    """
    if not entries:
        return None

    out_dir = output_dir or _DISCOVERIES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    slug = re.sub(r"[^a-z0-9-]", "-", project.lower())[:40].strip("-") or "unknown"
    filename = f"{today}-{slug}-negatives.md"
    output_path = out_dir / filename

    if output_path.exists():
        logger.debug("write_negatives: %s already exists, skipping", filename)
        from depthfusion.capture._metrics import emit_capture_event
        emit_capture_event(
            capture_mechanism="negative_extractor",
            project=project, session_id=session_id,
            write_success=False, entries_written=0,
            file_path=str(output_path),
        )
        return None

    lines = [
        "---",
        f"project: {project}",
        f"session_id: {session_id}",
        f"date: {today}",
        f"entries: {len(entries)}",
        "type: negative",
        "---",
        "",
        f"# Negative Signals: {project} — {today}",
        "",
    ]
    for entry in entries:
        what = entry.what
        why = entry.why
        conf = int(entry.confidence * 100)
        if why:
            lines.append(f"- **{what}** — {why} _(confidence: {conf}%)_")
        else:
            lines.append(f"- **{what}** _(confidence: {conf}%)_")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %d negative signals to %s", len(entries), output_path.name)

    # S-60 / T-187: emit capture event on successful write.
    from depthfusion.capture._metrics import emit_capture_event
    emit_capture_event(
        capture_mechanism="negative_extractor",
        project=project, session_id=session_id,
        write_success=True, entries_written=len(entries),
        file_path=str(output_path),
    )
    return output_path


def extract_and_write(
    content: str,
    project: str,
    session_id: str,
    output_dir: Path | None = None,
    backend: Any = None,
) -> Path | None:
    """Convenience: extract negative signals and write to discovery dir.

    Returns path on success, None if nothing written.
    """
    extractor = LLMNegativeExtractor(backend=backend)
    entries = extractor.extract(content, source_session=session_id)
    return write_negatives(entries, project=project, session_id=session_id,
                           output_dir=output_dir)


__all__ = [
    "NegativeEntry",
    "HeuristicNegativeExtractor",
    "LLMNegativeExtractor",
    "write_negatives",
    "extract_and_write",
]
