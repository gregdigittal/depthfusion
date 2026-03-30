# src/depthfusion/graph/extractor.py
"""Entity extraction from memory content.

RegexExtractor: instant, confidence=1.0, no API calls.
HaikuExtractor: async Haiku enrichment, confidence 0.70–0.95.
confidence_merge: deduplicates, regex takes precedence on collision.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from depthfusion.graph.types import Entity

logger = logging.getLogger(__name__)

# Regex patterns per entity type
_CAMEL_CASE_RE = re.compile(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b')
_SNAKE_FUNC_RE = re.compile(r'\b([a-z][a-z0-9_]{2,}\(\))')
_FILE_RE = re.compile(r'\b([a-z][a-z0-9_/\-]+\.py)\b')

_HAIKU_PROMPT = """\
Extract named entities from the following text. Return ONLY a JSON array.
Each element: {{"name": "<entity>", "type": "<concept|decision|error_pattern>"}}
Limit to the 10 most important. If none, return [].

Types:
- concept: technical term, algorithm, pattern (e.g. "BM25 scoring", "RRF fusion")
- decision: an architectural choice (e.g. "chose SQLite over ChromaDB")
- error_pattern: an error message or failure mode (e.g. "AttributeError: reranker")

Text:
{content}"""


def make_entity_id(name: str, type_: str, project: str) -> str:
    """Deterministic 12-char ID from sha256(name + type + project)."""
    raw = f"{name}{type_}{project}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class RegexExtractor:
    """Fast, no-API entity extraction. Returns confidence=1.0 entities."""

    def __init__(self, project: str):
        self._project = project

    def extract(self, content: str, source_file: str) -> list[Entity]:
        entities: list[Entity] = []
        seen: set[str] = set()

        for match in _CAMEL_CASE_RE.finditer(content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "class", self._project),
                    name=name, type="class", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={},
                ))

        for match in _SNAKE_FUNC_RE.finditer(content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "function", self._project),
                    name=name, type="function", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={},
                ))

        for match in _FILE_RE.finditer(content):
            name = match.group(1)
            if name not in seen and ("/" not in name or name.endswith(".py")):
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "file", self._project),
                    name=name, type="file", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={},
                ))

        return entities


class HaikuExtractor:
    """Haiku-based extraction for concepts, decisions, error_patterns.

    Returns empty list when DEPTHFUSION_HAIKU_ENABLED is not set or SDK unavailable.
    Reads DEPTHFUSION_API_KEY (preferred) or ANTHROPIC_API_KEY (legacy fallback).
    Confidence range: 0.70–0.95 (lower than regex to allow precedence).
    """

    def __init__(self, project: str, model: str = "claude-haiku-4-5-20251001"):
        self._project = project
        self._model = model
        self._client: Any = None
        haiku_enabled = os.environ.get("DEPTHFUSION_HAIKU_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        if not haiku_enabled:
            return
        try:
            import anthropic
            api_key = os.environ.get("DEPTHFUSION_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            pass

    def is_available(self) -> bool:
        return self._client is not None

    def extract(self, content: str, source_file: str) -> list[Entity]:
        if not self._client:
            return []
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": _HAIKU_PROMPT.format(content=content[:2000]),
                }],
            )
            raw = msg.content[0].text.strip()
            items: list[dict] = json.loads(raw)
        except (json.JSONDecodeError, Exception) as exc:
            logger.debug("HaikuExtractor failed: %s", exc)
            return []

        entities: list[Entity] = []
        for item in items[:10]:
            name = item.get("name", "").strip()
            etype = item.get("type", "concept")
            if not name:
                continue
            entities.append(Entity(
                entity_id=make_entity_id(name, etype, self._project),
                name=name, type=etype, project=self._project,
                source_files=[source_file], confidence=0.85,
                first_seen=_now_iso(), metadata={},
            ))
        return entities


def confidence_merge(
    regex_entities: list[Entity],
    haiku_entities: list[Entity],
) -> list[Entity]:
    """Merge two entity lists. Regex wins on ID collision (higher confidence).

    All entities are returned regardless of confidence — callers filter by threshold.
    """
    result: dict[str, Entity] = {}
    for e in haiku_entities:
        result[e.entity_id] = e
    for e in regex_entities:
        # Regex overwrites haiku on same ID (regex confidence = 1.0 > haiku)
        result[e.entity_id] = e
    return list(result.values())
