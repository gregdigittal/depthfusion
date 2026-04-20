"""Session tagger — reads .tmp session files and writes .meta.yaml sidecar files.

C1 SAFETY CONSTRAINT: This module NEVER modifies the source .tmp file.
The sidecar path is derived as: /path/to/session.tmp → /path/to/session.meta.yaml
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # see loader.py for rationale

# Known project name patterns (order matters — more specific first)
_PROJECT_PATTERNS: list[tuple[str, str]] = [
    (r"agreement[_\-]automation", "agreement_automation"),
    (r"virtual[_\-]analyst", "virtual_analyst"),
    (r"social[_\-]media", "social-media"),
    (r"depthfusion", "depthfusion"),
    (r"agent[_\-]mission[_\-]control", "agent-mission-control"),
    (r"skillforge", "skillforge"),
    (r"cc[_\-]connect", "cc-connect"),
]

# Category keyword sets (order matters — most distinctive first)
_CATEGORY_KEYWORDS: list[tuple[str, set[str]]] = [
    ("debugging", {"debug", "debugging", "error", "traceback", "bug", "fix", "exception",
                   "AttributeError", "TypeError", "ValueError", "KeyError", "stacktrace"}),
    ("refactor", {"refactor", "refactoring", "cleanup", "clean", "restructure", "rename",
                  "extract", "reorganize"}),
    ("planning", {"plan", "planning", "roadmap", "milestone", "sprint", "architecture",
                  "design", "strategy", "proposal", "spec"}),
    ("research", {"research", "investigate", "explore", "compare", "evaluate", "analysis",
                  "study", "review", "analysis", "options"}),
    ("feature", {"feature", "implement", "add", "build", "create", "new", "develop",
                 "integration", "endpoint", "api"}),
]

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "can", "this", "that", "these", "those", "it", "its",
    "i", "we", "you", "he", "she", "they", "my", "your", "our", "their",
    "not", "no", "so", "if", "as", "up", "out", "about", "into", "just",
    "also", "then", "than", "when", "where", "which", "who", "what", "how",
    "some", "any", "all", "more", "most", "such", "like", "there", "here",
}

)

# Patterns for entity detection
_PATH_PATTERN = re.compile(r"[\w./\-]+\.(?:py|ts|js|yaml|json|md|sh|toml)\b")
_CLASS_PATTERN = re.compile(r"\b[A-Z][a-zA-Z]{2,}[A-Z][a-zA-Z]*\b")  # CamelCase
_FUNC_PATTERN = re.compile(r"\b[a-z_][a-z0-9_]+\(\)")              # snake_case()


class SessionTagger:
    """Extracts tags from session content and writes .meta.yaml sidecar files.

    NEVER modifies the source .tmp file (C1 constraint).
    The sidecar path is: /path/to/session.tmp → /path/to/session.meta.yaml
    """

    def tag_session(self, session_path: Path) -> dict:
        """Read session_path (.tmp file), extract tags, write .meta.yaml sidecar.

        Returns the metadata dict written.
        Raises FileNotFoundError if session_path doesn't exist.

        C1 SAFETY: This method only READS session_path, never writes to it.
        """
        if not session_path.exists():
            raise FileNotFoundError(f"Session file not found: {session_path}")

        # C1: read-only access to the .tmp file
        content = session_path.read_text(encoding="utf-8", errors="replace")

        tags = self._extract_tags(content)

        metadata: dict = {
            "session_id": session_path.stem,
            "project": tags["project"],
            "category": tags["category"],
            "keywords": tags["keywords"],
            "entities": tags["entities"],
            "tagged_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        # Write sidecar — never touch the .tmp file
        sidecar_path = session_path.with_suffix(".meta.yaml")
        with sidecar_path.open("w", encoding="utf-8") as f:
            yaml.dump(metadata, f, default_flow_style=False, allow_unicode=True)

        return metadata

    def _extract_tags(self, content: str) -> dict:
        """Extract project, category, keywords, and entities from content."""
        return {
            "project": self._detect_project(content),
            "category": self._detect_category(content),
            "keywords": self._extract_keywords(content),
            "entities": self._extract_entities(content),
        }

    def _detect_project(self, content: str) -> str:
        content_lower = content.lower()
        for pattern, project_name in _PROJECT_PATTERNS:
            if re.search(pattern, content_lower):
                return project_name
        return "unknown"

    def _detect_category(self, content: str) -> str:
        content_lower = content.lower()
        words = set(re.findall(r"\b\w+\b", content_lower))
        # Also check original content for CamelCase error names
        words_orig = set(re.findall(r"\b\w+\b", content))

        best_category = "other"
        best_count = 0

        for category, keywords in _CATEGORY_KEYWORDS:
            # Count matches against lowercase words and original for error names
            count = len(keywords & words) + len(keywords & words_orig)
            if count > best_count:
                best_count = count
                best_category = category

        return best_category

    def _extract_keywords(self, content: str) -> list[str]:
        words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{2,}\b", content)
        # Frequency count, excluding stopwords
        freq: dict[str, int] = {}
        for word in words:
            lower = word.lower()
            if lower not in _STOPWORDS:
                freq[lower] = freq.get(lower, 0) + 1

        # Sort by frequency, take top 5
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in sorted_words[:5]]

    def _extract_entities(self, content: str) -> list[str]:
        entities: list[str] = []
        seen: set[str] = set()

        # File paths
        for match in _PATH_PATTERN.finditer(content):
            path = match.group()
            if path not in seen:
                entities.append(path)
                seen.add(path)

        # CamelCase class names
        for match in _CLASS_PATTERN.finditer(content):
            name = match.group()
            if name not in seen:
                entities.append(name)
                seen.add(name)

        # snake_case() function calls
        for match in _FUNC_PATTERN.finditer(content):
            name = match.group()
            if name not in seen:
                entities.append(name)
                seen.add(name)

        return entities
