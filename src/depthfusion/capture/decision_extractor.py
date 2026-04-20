"""decision_extractor.py — LLM-based structured decision capture (CM-1).

Extracts architectural decisions, implementation choices, and key facts from
session transcripts and writes them as discovery files tagged with project and
confidence.

CM-1 contract (S-45):
  - Precision ≥ 0.80 on labelled eval set (vs heuristic baseline ~0.60)
  - Each decision written to {date}-{project}-decisions.md with frontmatter
  - Idempotent: same session file → same output path → no duplicate write

Spec: docs/plans/v0.5/01-assessment.md §CM-1
Backlog: T-136, T-137, T-139
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

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "confidence": {"type": "number"},
                    "category": {"type": "string"},
                },
            },
        }
    },
}

_EXTRACT_PROMPT = """\
You are a precise knowledge extractor. From the following Claude Code session \
transcript, extract the key architectural decisions, implementation choices, \
configuration values (IPs, ports, versions, flags), error patterns, and \
established conventions.

For each item return:
  text: a concise statement of the decision/fact (max 200 chars)
  confidence: 0.0-1.0 (how clearly this was established vs tentative)
  category: one of: decision | fact | pattern | error_fix | value

Extract at most 15 items. Skip conversational filler and debugging noise.
Focus on durable knowledge that would help in a future session.

Session transcript:
{content}"""

_HEURISTIC_PATTERNS = [
    (r"^→\s+(.{10,300})", re.MULTILINE),
    (r"^DECISION:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^NOTE:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^IMPORTANT:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^WARNING:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^\*\*(.{10,150})\*\*", re.MULTILINE),
]

_DEFAULT_CONFIDENCE_HEURISTIC = 0.60


class DecisionEntry:
    """A single extracted decision with metadata."""

    __slots__ = ("text", "confidence", "category", "source_session")

    def __init__(
        self,
        text: str,
        confidence: float,
        category: str = "decision",
        source_session: str = "",
    ) -> None:
        self.text = text
        self.confidence = max(0.0, min(1.0, confidence))
        self.category = category
        self.source_session = source_session

    def __repr__(self) -> str:  # pragma: no cover
        return f"DecisionEntry({self.text[:60]!r}, conf={self.confidence:.2f})"


class HeuristicDecisionExtractor:
    """Regex-based fallback extractor. No API calls. Precision ~0.60."""

    def extract(self, content: str, source_session: str = "") -> list[DecisionEntry]:
        if not content or len(content.strip()) < 20:
            return []
        results: list[DecisionEntry] = []
        seen: set[str] = set()
        for pattern, flags in _HEURISTIC_PATTERNS:
            for m in re.finditer(pattern, content, flags):
                text = m.group(1).strip()
                if len(text) >= 10 and text not in seen:
                    seen.add(text)
                    results.append(DecisionEntry(
                        text=text,
                        confidence=_DEFAULT_CONFIDENCE_HEURISTIC,
                        category="decision",
                        source_session=source_session,
                    ))
                    if len(results) >= 50:
                        break
        return results[:50]


class LLMDecisionExtractor:
    """LLM-backed extractor. Precision target ≥ 0.80 (CM-1 AC-1).

    Uses the `decision_extractor` capability from the backend factory.
    Falls back to HeuristicDecisionExtractor when the backend is unavailable.
    """

    def __init__(self, backend: Any = None) -> None:
        self._backend: Any = None

        if backend is not None:
            # Test injection
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

    def extract(self, content: str, source_session: str = "") -> list[DecisionEntry]:
        """Extract decisions from content using LLM or heuristic fallback."""
        if not self.is_available():
            return HeuristicDecisionExtractor().extract(content, source_session)

        try:
            result = self._backend.extract_structured(
                _EXTRACT_PROMPT.format(content=content[:4000]),
                schema=_DECISION_SCHEMA,
            )
        except Exception as exc:  # noqa: BLE001 — graceful-degradation contract
            logger.debug("LLM decision extractor failed (%s), falling back to heuristic", exc)
            return HeuristicDecisionExtractor().extract(content, source_session)

        if not result or not isinstance(result.get("decisions"), list):
            return HeuristicDecisionExtractor().extract(content, source_session)

        entries: list[DecisionEntry] = []
        seen: set[str] = set()
        for item in result["decisions"]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if len(text) < 10 or text in seen:
                continue
            confidence = float(item.get("confidence", 0.7))
            category = str(item.get("category", "decision"))
            entries.append(DecisionEntry(
                text=text,
                confidence=max(0.0, min(1.0, confidence)),
                category=category,
                source_session=source_session,
            ))
            seen.add(text)
            if len(entries) >= 15:
                break

        return entries if entries else HeuristicDecisionExtractor().extract(content, source_session)


def write_decisions(
    entries: list[DecisionEntry],
    project: str,
    session_id: str,
    output_dir: Path | None = None,
    min_confidence: float = 0.0,
) -> Path | None:
    """Write a list of DecisionEntry objects to a discovery file.

    File name: {YYYY-MM-DD}-{project}-decisions.md
    Idempotent: if the output file already exists, does not overwrite it.

    Args:
        entries: list of DecisionEntry objects to write
        project: project slug for the frontmatter
        session_id: session identifier for frontmatter
        output_dir: directory to write to (default: ~/.claude/shared/discoveries/)
        min_confidence: skip entries with confidence < min_confidence

    Returns:
        Path to the written file, or None if nothing to write or already exists.
    """
    filtered = [e for e in entries if e.confidence >= min_confidence]
    if not filtered:
        return None

    out_dir = output_dir or _DISCOVERIES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    # Sanitize project slug
    slug = re.sub(r"[^a-z0-9-]", "-", project.lower())[:40].strip("-") or "unknown"
    filename = f"{today}-{slug}-decisions.md"
    output_path = out_dir / filename

    # Idempotent: don't overwrite
    if output_path.exists():
        logger.debug("write_decisions: %s already exists, skipping", filename)
        return None

    lines = [
        "---",
        f"project: {project}",
        f"session_id: {session_id}",
        f"date: {today}",
        f"entries: {len(filtered)}",
        "type: decisions",
        "---",
        "",
        f"# Decisions: {project} — {today}",
        "",
    ]
    for entry in filtered:
        conf_pct = int(entry.confidence * 100)
        lines.append(
            f"- [{entry.category}] {entry.text}"
            + (f" _(confidence: {conf_pct}%)_" if entry.confidence < 1.0 else "")
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %d decisions to %s", len(filtered), output_path.name)
    return output_path


def extract_and_write(
    content: str,
    project: str,
    session_id: str,
    output_dir: Path | None = None,
    backend: Any = None,
) -> Path | None:
    """Convenience: extract decisions from content and write to discovery dir.

    This is the main entry point used by the Stop hook and SessionCompressor.

    Returns the output Path on success, None if nothing written.
    """
    extractor = LLMDecisionExtractor(backend=backend)
    entries = extractor.extract(content, source_session=session_id)
    return write_decisions(entries, project=project, session_id=session_id,
                           output_dir=output_dir)


__all__ = [
    "DecisionEntry",
    "HeuristicDecisionExtractor",
    "LLMDecisionExtractor",
    "write_decisions",
    "extract_and_write",
]
