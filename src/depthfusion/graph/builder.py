# src/depthfusion/graph/builder.py
"""Document entity-extraction pipeline for ingested document chunks.

T-618: DocumentEntityBuilder calls an LLM (Haiku) to extract named entities
from ingested DocumentRecord chunks, inheriting the source document's ACL onto
every emitted Entity (T-619).

Design notes
------------
* Mirrors the HaikuExtractor ergonomics from graph/extractor.py:
  - lazy / optional anthropic import via the backend factory
  - is_available() guard so the class is always constructable without a key
  - offline-safe: when the LLM backend is unavailable the builder falls back
    to RegexExtractor output (or an empty list for pure-concept chunks)
* When DEPTHFUSION_HAIKU_ENABLED is not set (or is "false"), the builder runs
  in regex-only mode — no network, no crash.
* Inject a mock backend via ``haiku_backend=`` in tests to exercise the LLM
  path deterministically.
"""
from __future__ import annotations

from typing import Any

from depthfusion.graph.extractor import DocumentEntityPipeline
from depthfusion.graph.types import Entity


class DocumentEntityBuilder:
    """Extract named entities from document chunks with ACL inheritance.

    Parameters
    ----------
    project:
        The DepthFusion project slug that owns the document.
    haiku_backend:
        Optional injected LLM backend (for testing). When *None* the
        env-gated factory resolves the real backend (or nothing if
        DEPTHFUSION_HAIKU_ENABLED is unset / "false").

    Usage
    -----
    ::

        builder = DocumentEntityBuilder(project="acme")
        entities = builder.extract(
            chunk_text="The RRF fusion algorithm is used by RecallPipeline.",
            source_file="docs/architecture.md",
            acl_allow=["acme-corp", "engineering"],
        )
        # Every entity carries metadata["acl_allow"] == ["acme-corp", "engineering"]
    """

    def __init__(self, project: str, haiku_backend: Any = None) -> None:
        self._project = project
        self._pipeline = DocumentEntityPipeline(
            project=project,
            haiku_backend=haiku_backend,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True when the LLM backend will be consulted."""
        return self._pipeline.llm_available()

    def extract(
        self,
        chunk_text: str,
        source_file: str,
        acl_allow: list[str] | None = None,
    ) -> list[Entity]:
        """Extract named entities from a document chunk.

        Parameters
        ----------
        chunk_text:
            The plain-text content of a single document chunk.
        source_file:
            The source document path/identifier (stored in Entity.source_files).
        acl_allow:
            The source document's access-control list.  Every returned entity
            inherits this ACL via ``metadata["acl_allow"]`` (T-619). When
            *None*, entities are scoped to ``[project]`` (backward-compat
            fallback).

        Returns
        -------
        list[Entity]
            Merged and deduplicated entity list (regex + LLM when available).
            Returns an empty list on empty input; never raises.
        """
        if not chunk_text or not chunk_text.strip():
            return []
        return self._pipeline.extract(
            content=chunk_text,
            source_file=source_file,
            acl_allow=acl_allow,
        )

    # Alias so callers can use either ``extract`` or ``build``
    build = extract
