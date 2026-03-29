"""Auto-learn extraction — heuristic (local) and haiku-based (VPS).

HeuristicExtractor: regex-based extraction of key decisions from .tmp files.
No API calls — safe for local mode.

HaikuSummarizer: calls Claude haiku to produce a structured discovery summary.
Requires ANTHROPIC_API_KEY. Used in VPS mode by PostCompact hook.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DECISION_PATTERNS = [
    (r"^→\s+(.{10,300})", re.MULTILINE),
    (r"^DECISION:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^NOTE:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^IMPORTANT:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^WARNING:\s*(.{10,300})", re.MULTILINE | re.IGNORECASE),
    (r"^\*\*(.{10,150})\*\*", re.MULTILINE),
]

_MIN_CONTENT_LENGTH = 20


def extract_key_decisions(content: str) -> list[str]:
    """Extract decision-like lines from session content using heuristic patterns."""
    if not content or len(content.strip()) < _MIN_CONTENT_LENGTH:
        return []
    decisions: list[str] = []
    for pattern, flags in _DECISION_PATTERNS:
        try:
            matches = re.findall(pattern, content, flags)
            decisions.extend(m.strip() for m in matches if len(m.strip()) >= 10)
        except Exception:
            continue
    # Deduplicate preserving order, cap at 50
    seen: set[str] = set()
    unique: list[str] = []
    for d in decisions:
        if d not in seen:
            seen.add(d)
            unique.append(d)
        if len(unique) >= 50:
            break
    return unique


class HeuristicExtractor:
    """Extracts key decisions from a .tmp session file without API calls."""

    def extract_from_file(self, path: Path) -> str | None:
        """Read file and return a markdown summary string, or None if nothing found."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        decisions = extract_key_decisions(content)
        if not decisions:
            return None
        lines = [f"# Auto-Learned: {path.stem}", ""]
        lines.extend(f"- {d}" for d in decisions)
        return "\n".join(lines)


class HaikuSummarizer:
    """Summarize a .tmp session file into a structured discovery using Claude haiku.

    Requires anthropic SDK and ANTHROPIC_API_KEY. Gracefully degrades to
    HeuristicExtractor when unavailable.
    """

    _PROMPT = """\
Extract the key architectural decisions, facts, and implementation choices from this \
Claude Code session transcript. Focus on: decisions made, errors encountered and fixed, \
specific values (IPs, keys, versions), and patterns established. Ignore conversational \
filler. Format as a concise markdown document with ## sections.

Session content (truncated to 3000 chars):
{content}"""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self._client = None
        try:
            import anthropic
            import os
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except ImportError:
            pass

    def is_available(self) -> bool:
        return self._client is not None

    def summarize_file(self, path: Path) -> str | None:
        """Summarize a session file. Falls back to heuristic if haiku unavailable."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not content.strip():
            return None

        if not self.is_available():
            return HeuristicExtractor().extract_from_file(path)

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": self._PROMPT.format(content=content[:3000]),
                }],
            )
            summary = msg.content[0].text.strip()
            if not summary:
                return None
            return f"# Session Summary: {path.stem}\n\n{summary}"
        except Exception as exc:
            logger.warning("Haiku summarizer failed (%s), falling back to heuristic", exc)
            return HeuristicExtractor().extract_from_file(path)


def summarize_and_extract_graph(
    path: Path,
    project: str,
    graph_store: "Any | None",
) -> None:
    """Run HaikuSummarizer + graph entity extraction on a session file.

    Stores extracted entities and co-occurrence edges into graph_store.
    No-ops silently when DEPTHFUSION_GRAPH_ENABLED is not 'true' or graph_store is None.
    """
    import os

    # Always run the summarizer (existing behaviour is unchanged)
    HaikuSummarizer().summarize_file(path)

    if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() != "true":
        return
    if graph_store is None:
        return

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    try:
        from depthfusion.graph.extractor import (
            RegexExtractor, HaikuExtractor, confidence_merge,
        )
        from depthfusion.graph.linker import CoOccurrenceLinker

        regex_ext = RegexExtractor(project=project)
        regex_entities = regex_ext.extract(content, source_file=str(path))
        haiku_ext = HaikuExtractor(project=project)
        haiku_entities = haiku_ext.extract(content, source_file=str(path))
        entities = confidence_merge(regex_entities, haiku_entities)

        linker = CoOccurrenceLinker()
        edges = linker.link(entities)

        for entity in entities:
            graph_store.upsert_entity(entity)
        for edge in edges:
            graph_store.upsert_edge(edge)
    except Exception as exc:
        logger.debug("Graph entity extraction failed: %s", exc)
