"""Session compactor — reduces session content by relevance to a task description."""
from __future__ import annotations

import re


def _score_section(section: str, task_tokens: set[str]) -> float:
    """Score a section by keyword overlap with task tokens."""
    if not task_tokens:
        return 0.0
    words = set(re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{1,}\b", section.lower()))
    overlap = len(words & task_tokens)
    return overlap / len(task_tokens)


def _tokenize_task(task_description: str) -> set[str]:
    _STOPWORDS = frozenset({
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "was", "are", "be", "this", "that",
    })
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{1,}\b", task_description.lower())
    return {w for w in words if w not in _STOPWORDS}


class SessionCompactor:
    """Reduces session content, preserving sections most relevant to the task.

    Sections are split on blank-line boundaries (paragraph-style).
    High relevance → preserve verbatim.
    Medium relevance → compress to 1-2 line summary.
    Low relevance → drop.
    """

    def __init__(self, preserve_ratio: float = 0.5) -> None:
        if not 0.0 <= preserve_ratio <= 1.0:
            raise ValueError(f"preserve_ratio must be in [0.0, 1.0], got {preserve_ratio}")
        self._preserve_ratio = preserve_ratio

    def compact(self, content: str, task_description: str) -> str:
        """Score sections by relevance to task_description and compact accordingly."""
        if not content or not content.strip():
            return ""

        # Split into sections on blank lines
        sections = re.split(r"\n\s*\n", content)
        sections = [s.strip() for s in sections if s.strip()]

        if not sections:
            return ""

        # With preserve_ratio=1.0, return all content
        if self._preserve_ratio >= 1.0:
            return "\n\n".join(sections)

        task_tokens = _tokenize_task(task_description)

        # Score each section
        scored = [(section, _score_section(section, task_tokens)) for section in sections]

        # Determine thresholds
        scores = sorted(s for _, s in scored)
        n = len(scores)

        # With preserve_ratio=0.0, nothing is "high" relevance
        high_threshold_idx = max(0, int(n * (1.0 - self._preserve_ratio)))
        high_threshold = scores[high_threshold_idx] if high_threshold_idx < n else float("inf")

        # Medium: lower half of the preserve_ratio range
        mid_threshold_idx = max(0, int(n * 0.3))
        mid_threshold = scores[mid_threshold_idx] if mid_threshold_idx < n else 0.0

        output_parts: list[str] = []
        for section, score in scored:
            if self._preserve_ratio == 0.0:
                # Drop everything (no high-relevance tier possible)
                if score > 0.0:
                    # Still show a brief summary for anything with any match
                    first_line = section.split("\n")[0]
                    output_parts.append(f"[summary] {first_line[:80]}")
                # else drop entirely
            elif score >= high_threshold and score > 0.0:
                # High relevance — preserve verbatim
                output_parts.append(section)
            elif score >= mid_threshold and score > 0.0:
                # Medium relevance — compress to first 1-2 lines
                lines = section.split("\n")
                summary = " ".join(lines[:2])[:120]
                output_parts.append(f"[summary] {summary}")
            # else: drop (low/zero relevance)

        return "\n\n".join(output_parts)
