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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def _default_discoveries_dir() -> Path:
    """Resolve `~/.claude/shared/discoveries/` at call time.

    Using a function (not a module-level constant) lets tests redirect
    `Path.home()` via monkeypatch after the module is imported — a
    module-level constant would freeze the real home directory at
    import time and ignore the patch. Same pattern as `capture/pruner.py`
    and `install/install.py`.
    """
    return Path.home() / ".claude" / "shared" / "discoveries"


# Deprecated module-level constant — retained for any external caller
# that still imports it. New code should use `_default_discoveries_dir()`.
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

# Explicit annotation markers: human intent is declared; no length filter applied.
_ANNOTATED_PATTERNS: list[tuple[str, int]] = [
    (r"^→\s+(.{10,300})", re.MULTILINE),
    (r"^DECISION:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^NOTE:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^IMPORTANT:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^WARNING:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^\*\*(.{10,150})\*\*", re.MULTILINE),
]

# Prose heuristic patterns: length and topic filters are applied to reduce FPs.
_PROSE_PATTERNS: list[tuple[str, int]] = [
    # Commitment verbs — "going with X", "settled on X", "decided to X"
    (r"(?:I'?m |we'?re |going )?going with\s+(.{10,200}?)(?:[.;,\n]|$)", re.IGNORECASE),
    (r"settled on\s+(.{10,200}?)(?:[.;,\n]|$)", re.IGNORECASE),
    (r"decided(?:\s+to)?\s*:?\s+(.{10,200}?)(?:[.;,\n]|$)", re.IGNORECASE),
    (r"(?:we |I )?chose\s+(.{10,200}?)(?:[.;,\n]|$)", re.IGNORECASE),
    (r"(?:the )?decision(?:\s+is|:)\s+(.{10,200}?)(?:[.;,\n]|$)", re.IGNORECASE),
    # Architecture/policy statements — "Use X for Y", "Policy: X", "Standard: X"
    (r"^(?:Use|Using)\s+(.{10,200}?)(?:[.;]|$)", re.MULTILINE | re.IGNORECASE),
    (r"^Policy:\s*(.{10,200}?)(?:[.;]|$)", re.MULTILINE | re.IGNORECASE),
    (r"^Standard(?:\s+rule)?:\s*(.{10,200}?)(?:[.;]|$)", re.MULTILINE | re.IGNORECASE),
    (r"^(?:All|Every)\s+(.{10,200}?must.{5,100}?)(?:[.;]|$)", re.MULTILINE | re.IGNORECASE),
    # Migration / switch / replacement decisions
    (r"migrat(?:e|ed|ing)\s+(?:from\s+\S+\s+)?to\s+(.{10,150}?)(?:[.;,\n]|$)", re.IGNORECASE),
    (r"switch(?:ed|ing)\s+(?:from\s+\S+\s+)?to\s+(.{10,150}?)(?:[.;,\n]|$)", re.IGNORECASE),
    (r"^Replacing\s+(.{10,200}?)(?:[.;]|$)", re.MULTILINE | re.IGNORECASE),
    # Chosen-over comparisons — "X over Y because Z"
    (
        r"(.{10,100}?)\s+over\s+\S.{5,80}?\s+(?:because|since|as)\s+.{5,100}?(?:[.;]|$)",
        re.IGNORECASE,
    ),
]

# Combined list kept for backwards-compat callers that reference _HEURISTIC_PATTERNS.
_HEURISTIC_PATTERNS = _ANNOTATED_PATTERNS + _PROSE_PATTERNS

# Regex patterns for topic-description false positives — phrases that describe
# the subject of a decision rather than the decision itself.
_FP_TOPIC_PATTERNS = [
    re.compile(r"^the following", re.IGNORECASE),
    re.compile(r"^where\s+to\s+", re.IGNORECASE),
    re.compile(r"^how\s+to\s+", re.IGNORECASE),
    re.compile(r"^on\s+the\s+", re.IGNORECASE),
    re.compile(
        r"^the\s+(?:\w+[\s-]){0,3}(?:toolchain|stack|approach|design|system|solution|architecture)\b",
        re.IGNORECASE,
    ),
]

_MIN_DECISION_LENGTH = 25

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
        raw: list[DecisionEntry] = []
        seen: set[str] = set()

        for pattern_list, apply_filters in (
            (_ANNOTATED_PATTERNS, False),
            (_PROSE_PATTERNS, True),
        ):
            for pattern, flags in pattern_list:
                for m in re.finditer(pattern, content, flags):
                    text = m.group(1).strip()
                    if text in seen:
                        continue
                    if apply_filters:
                        if len(text) < _MIN_DECISION_LENGTH:
                            continue
                        if any(p.search(text) for p in _FP_TOPIC_PATTERNS):
                            continue
                    seen.add(text)
                    raw.append(DecisionEntry(
                        text=text,
                        confidence=_DEFAULT_CONFIDENCE_HEURISTIC,
                        category="decision",
                        source_session=source_session,
                    ))
                    if len(raw) >= 50:
                        break

        # Substring deduplication: keep the longest representative when one
        # entry is a substring of another (greedy-match FP suppression).
        raw.sort(key=lambda e: len(e.text), reverse=True)
        results: list[DecisionEntry] = []
        kept_texts: list[str] = []
        for entry in raw:
            lower = entry.text.lower()
            if any(lower in kept.lower() for kept in kept_texts):
                continue
            kept_texts.append(entry.text)
            results.append(entry)

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
    capture_mechanism: str = "decision_extractor",
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
        capture_mechanism: S-60 — name emitted in the metrics stream.
            Defaults to "decision_extractor" for direct calls; callers
            that wrap this function (e.g. `_tool_confirm_discovery`)
            override with their own mechanism name so the metrics bucket
            reflects the HIGHER-LEVEL intent, not the internal writer.

    Returns:
        Path to the written file, or None if nothing to write or already exists.
    """
    filtered = [e for e in entries if e.confidence >= min_confidence]
    if not filtered:
        return None

    out_dir = output_dir or _default_discoveries_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    # Sanitize project slug
    slug = re.sub(r"[^a-z0-9-]", "-", project.lower())[:40].strip("-") or "unknown"
    filename = f"{today}-{slug}-decisions.md"
    output_path = out_dir / filename

    # Idempotent: don't overwrite. Emit a capture event with
    # `event_subtype="ok"` + `write_success=False` so the skip shows up
    # in the metrics stream as a legitimate outcome (not an error).
    if output_path.exists():
        logger.debug("write_decisions: %s already exists, skipping", filename)
        from depthfusion.capture._metrics import emit_capture_event
        emit_capture_event(
            capture_mechanism=capture_mechanism,
            project=project, session_id=session_id,
            write_success=False, entries_written=0,
            file_path=str(output_path),
        )
        return None

    # S-70 — file-level importance is the MAX of per-entry confidence
    # (loudest signal wins; see test_aggregate_importance_uses_max_confidence
    # for the documented design choice). Salience defaults to 1.0 at
    # capture time; S-72 recall feedback mutates it post-capture. Both
    # values are formatted with ``:.4f`` to stay byte-identical to what
    # ``_splice_memory_score_frontmatter`` writes — set_memory_score on a
    # freshly-captured file should produce no diff churn for unchanged
    # fields (consensus Round 1, Commit 3).
    from depthfusion.core.types import DEFAULT_SALIENCE
    aggregate_importance = max((e.confidence for e in filtered), default=0.5)

    lines = [
        "---",
        f"project: {project}",
        f"session_id: {session_id}",
        f"date: {today}",
        f"valid_from: {datetime.now(tz=timezone.utc).isoformat()}",
        f"entries: {len(filtered)}",
        "type: decisions",
        f"importance: {aggregate_importance:.4f}",
        f"salience: {DEFAULT_SALIENCE:.4f}",
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

    # S-60 / T-187: emit capture event on successful write.
    from depthfusion.capture._metrics import emit_capture_event
    emit_capture_event(
        capture_mechanism=capture_mechanism,
        project=project, session_id=session_id,
        write_success=True, entries_written=len(filtered),
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
