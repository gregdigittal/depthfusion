"""Auto-learn extraction — heuristic (local) and haiku-based (VPS).

HeuristicExtractor: regex-based extraction of key decisions from .tmp files.
No API calls — safe for local mode.

HaikuSummarizer: calls Claude haiku to produce a structured discovery summary.
Opt-in: requires DEPTHFUSION_HAIKU_ENABLED=true and DEPTHFUSION_API_KEY (or the
legacy ANTHROPIC_API_KEY fallback). Used in VPS mode by PostCompact hook.

⚠️  Do NOT set ANTHROPIC_API_KEY in ~/.claude/settings.json or your shell
environment — Claude Code reads it as auth and will switch your billing from
your Pro/Max subscription to pay-per-token API billing for ALL usage. Use
DEPTHFUSION_API_KEY instead.
"""
from __future__ import annotations

import logging
import os
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
    """Summarize a .tmp session file into a structured discovery.

    v0.5.0 T-120: migrated to the provider-agnostic backend interface. The
    summariser is still gated on `DEPTHFUSION_HAIKU_ENABLED` (preserving
    v0.4.x opt-in semantics) — when disabled, the backend is never resolved
    and `is_available()` returns False, so callers fall back to
    HeuristicExtractor.
    """

    _PROMPT = """\
Extract the key architectural decisions, facts, and implementation choices from this \
Claude Code session transcript. Focus on: decisions made, errors encountered and fixed, \
specific values (IPs, keys, versions), and patterns established. Ignore conversational \
filler. Format as a concise markdown document with ## sections.

Session content (truncated to 3000 chars):
{content}"""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        backend: Any = None,
    ) -> None:
        self._model = model
        self._backend: Any = None

        if backend is not None:
            # Test injection — bypass the env-var gate
            self._backend = backend
            return

        # v0.4.x opt-in gate preserved
        haiku_flag = os.environ.get("DEPTHFUSION_HAIKU_ENABLED", "false").strip().lower()
        if haiku_flag not in ("true", "1", "yes"):
            return

        from depthfusion.backends.factory import get_backend
        self._backend = get_backend("summariser")

    def is_available(self) -> bool:
        return self._backend is not None and self._backend.healthy()

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
            summary = self._backend.complete(
                self._PROMPT.format(content=content[:3000]),
                max_tokens=1024,
            )
            summary = (summary or "").strip()
            if not summary:
                return None
            return f"# Session Summary: {path.stem}\n\n{summary}"
        except Exception as exc:  # noqa: BLE001 — graceful-degradation contract
            logger.warning("Haiku summarizer failed (%s), falling back to heuristic", exc)
            return HeuristicExtractor().extract_from_file(path)


def summarize_and_extract_graph(
    path: Path,
    project: str,
    graph_store: "Any | None",
) -> None:
    """Run HaikuSummarizer + decision/negative extractors + graph entity extraction.

    T-137/T-147: wire decision_extractor and negative_extractor into the
    capture pipeline. Both run after the summarizer; errors are swallowed so
    a failing extractor never blocks the session compressor.

    Stores extracted entities and co-occurrence edges into graph_store.
    No-ops silently when DEPTHFUSION_GRAPH_ENABLED is not 'true' or graph_store is None.
    """
    # Phase 1: run the summarizer (existing behaviour — unchanged)
    HaikuSummarizer().summarize_file(path)

    # Phase 2: LLM decision extractor + negative extractor (v0.5 CM-1/CM-6)
    # Gated on DEPTHFUSION_DECISION_EXTRACTOR_ENABLED to avoid API calls in local mode.
    # After each write, run embedding-based dedup (T-150, CM-2) so semantic
    # duplicates from past sessions are superseded rather than accumulated.
    written_paths: list[Any] = []
    _extractor_flag = os.environ.get("DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", "false").lower()
    if _extractor_flag in ("true", "1", "yes"):
        # Single shared read so both extractors see the same content and
        # neither depends on the other's success. If the read itself fails,
        # skip extractor phase entirely.
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("Extractor phase: could not read %s: %s", path.name, exc)
            content = ""
        session_id = path.stem

        if content:
            try:
                from depthfusion.capture.decision_extractor import (
                    extract_and_write as _write_decisions,
                )
                out = _write_decisions(content=content, project=project, session_id=session_id)
                if out is not None:
                    written_paths.append(out)
            except Exception as exc:
                logger.debug("Decision extractor failed for %s: %s", path.name, exc)

            try:
                from depthfusion.capture.negative_extractor import (
                    extract_and_write as _write_negatives,
                )
                out = _write_negatives(content=content, project=project, session_id=session_id)
                if out is not None:
                    written_paths.append(out)
            except Exception as exc:
                logger.debug("Negative extractor failed for %s: %s", path.name, exc)

        # Phase 2b: dedup the newly-written files against the recent corpus.
        # Opt-in: DEPTHFUSION_DEDUP_ENABLED (default true — safe no-op when
        # the embedding backend is NullBackend / sentence-transformers missing).
        if os.environ.get("DEPTHFUSION_DEDUP_ENABLED", "true").lower() in ("true", "1", "yes"):
            try:
                from depthfusion.capture.dedup import dedup_against_corpus
                for written in written_paths:
                    dedup_against_corpus(written)
            except Exception as exc:
                logger.debug("Discovery dedup failed: %s", exc)

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
            HaikuExtractor,
            RegexExtractor,
            confidence_merge,
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
