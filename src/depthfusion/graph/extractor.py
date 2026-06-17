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


def _resolve_acl(acl_allow: list[str] | None, project: str) -> list[str]:
    """T-619: pick the ACL to stamp onto an extracted entity/edge.

    When the caller supplies a source-document ACL (`acl_allow`), the entity
    *inherits* it — this is the ACL-inheritance contract. When no ACL is
    supplied the extractor falls back to `[project]`, preserving the v0.4.x
    behaviour where memory entities are scoped to their own project.

    The returned list is always a fresh copy so two entities never share a
    mutable ACL reference.
    """
    if acl_allow:
        return list(acl_allow)
    return [project]


class RegexExtractor:
    """Fast, no-API entity extraction. Returns confidence=1.0 entities."""

    def __init__(self, project: str):
        self._project = project

    def extract(
        self,
        content: str,
        source_file: str,
        acl_allow: list[str] | None = None,
    ) -> list[Entity]:
        """Extract entities from `content`.

        `acl_allow` (T-619): the source document's ACL. When provided every
        extracted entity inherits it via `metadata["acl_allow"]`; otherwise
        the extractor falls back to `[project]`.
        """
        acl = _resolve_acl(acl_allow, self._project)
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
                    first_seen=_now_iso(), metadata={"acl_allow": list(acl)},
                ))

        for match in _SNAKE_FUNC_RE.finditer(content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "function", self._project),
                    name=name, type="function", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={"acl_allow": list(acl)},
                ))

        for match in _FILE_RE.finditer(content):
            name = match.group(1)
            if name not in seen and ("/" not in name or name.endswith(".py")):
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "file", self._project),
                    name=name, type="file", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={"acl_allow": list(acl)},
                ))

        return entities


class HaikuExtractor:
    """Haiku-based extraction for concepts, decisions, error_patterns.

    v0.5.0 T-120: uses the provider-agnostic backend interface via the
    factory (`get_backend("extractor")`). Remains gated on
    `DEPTHFUSION_HAIKU_ENABLED` so local installs don't call the backend
    even when a key is present — this preserves v0.4.x opt-in semantics.

    Returns empty list when the backend is unavailable (no API key /
    no SDK / HAIKU_ENABLED=false). Confidence range: 0.70–0.95 (lower
    than regex to allow precedence).
    """

    def __init__(
        self,
        project: str,
        model: str = "claude-haiku-4-5-20251001",
        backend: Any = None,
    ) -> None:
        self._project = project
        # `model` retained for compatibility; the backend owns model selection.
        self._model = model
        self._backend: Any = None

        # v0.4.x opt-in gate — do NOT call the backend unless explicitly enabled.
        haiku_flag = os.environ.get("DEPTHFUSION_HAIKU_ENABLED", "false").strip().lower()
        haiku_enabled = haiku_flag in ("true", "1", "yes")
        if backend is not None:
            # Test injection — bypass the env-var gate
            self._backend = backend
            return
        if not haiku_enabled:
            return

        from depthfusion.backends.factory import get_backend
        self._backend = get_backend("extractor")

    def is_available(self) -> bool:
        return self._backend is not None and self._backend.healthy()

    def extract(
        self,
        content: str,
        source_file: str,
        acl_allow: list[str] | None = None,
    ) -> list[Entity]:
        """Extract named entities via the LLM backend.

        `acl_allow` (T-619): the source document's ACL, inherited by every
        extracted entity. Falls back to `[project]` when not provided.
        Returns [] when the backend is unavailable (regex fallback applies
        at the pipeline level — see DocumentEntityPipeline).
        """
        if not self.is_available():
            return []
        acl = _resolve_acl(acl_allow, self._project)
        try:
            raw = self._backend.complete(
                _HAIKU_PROMPT.format(content=content[:2000]),
                max_tokens=512,
            )
            if not raw:
                return []
            items: list[dict] = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — graceful-degradation contract
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
                first_seen=_now_iso(), metadata={"acl_allow": list(acl)},
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


class DocumentEntityPipeline:
    """T-618: extract named entities from document content.

    Runs the regex extractor (always) and the LLM-backed HaikuExtractor
    (only when its backend is available), then merges the two. When the LLM
    backend is unavailable — no API key, no SDK, or DEPTHFUSION_HAIKU_ENABLED
    unset — the pipeline degrades gracefully to the regex-only fallback path,
    so document ingestion never depends on a live LLM call.

    T-619: every entity inherits the *source document's* ``acl_allow`` (passed
    per ``extract()`` call), satisfying the graph store's required-ACL rule
    enforced by ``_validate_graph_acl``.

    Inject a backend via ``haiku_backend=`` in tests to exercise the LLM path
    deterministically; pass ``haiku_backend=None`` (default) in production to
    let the env-gated factory resolve the real backend.
    """

    def __init__(self, project: str, haiku_backend: Any = None) -> None:
        self._project = project
        self._regex = RegexExtractor(project=project)
        self._haiku = HaikuExtractor(project=project, backend=haiku_backend)

    def llm_available(self) -> bool:
        """True when the LLM backend will be consulted; else regex-only."""
        return self._haiku.is_available()

    def extract(
        self,
        content: str,
        source_file: str,
        acl_allow: list[str] | None = None,
    ) -> list[Entity]:
        """Extract named entities from ``content``.

        ``acl_allow`` is the source document's ACL; every returned entity
        inherits it (T-619). Returns a merged, deduplicated entity list
        (regex precedence on collision via ``confidence_merge``).
        """
        regex_entities = self._regex.extract(content, source_file, acl_allow=acl_allow)
        # Regex fallback: when the LLM backend is down, haiku_entities is [].
        haiku_entities = self._haiku.extract(content, source_file, acl_allow=acl_allow)
        return confidence_merge(regex_entities, haiku_entities)
