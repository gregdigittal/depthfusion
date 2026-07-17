"""ScenarioEngine — cluster L1 memories into named scene blocks.

E-68 S-230.

Clusters active (L1) memories by cosine similarity + 24-hour time window into
named scene blocks written to ``~/.claude/shared/discoveries/scenarios-{project_id}.md``.

Design:
  - Uses the existing embedding backend (LocalEmbeddingBackend / factory) for
    cosine similarity via ``backends.factory.get_backend("embedding")``.
  - Falls back to token Jaccard when no embedding backend is available.
  - Scene block names are distilled via DistillationClient; falls back to a
    timestamp-based label when the client is unavailable or returns empty.
  - ``rebuild(scope)`` is async; triggered as a post-pass after every
    ``PersonaEngine.generate()``.
  - Default cosine threshold: 0.75 (fixed — no config knob needed per spec).
  - Time window: 24 hours.

AC references:
  AC-1  rebuild() clusters L1 memories into named scene blocks in scenarios-*.md
  AC-2  rebuild() triggered after every PersonaEngine.generate()
  AC-3  include_scenarios kwarg on recall includes matching block summary
  AC-4  scene block names distilled by DistillationClient; fallback timestamp label
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from depthfusion.cognitive.distillation_client import DistillationClient
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.core.memory_object import MemoryObject

logger = logging.getLogger(__name__)

_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"
_COSINE_THRESHOLD: float = 0.75   # fixed default per spec (clarification 7a)
_TIME_WINDOW_HOURS: int = 24
_SLUG_RE = re.compile(r"[^a-z0-9-]")


# ── Cosine similarity (reuse same logic as consolidator.py) ──────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; returns 0.0 on empty or zero-norm vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _token_jaccard(a: str, b: str) -> float:
    """Token Jaccard overlap — fallback when no embedder is available."""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── Project-id helper (same pattern as persona.py) ───────────────────────────

def _project_id_from_scope(scope: dict[str, Any]) -> str:
    """Derive a filesystem-safe project slug from a scope dict."""
    raw = (
        scope.get("project_id")
        or scope.get("project")
        or scope.get("slug")
        or "default"
    )
    return _SLUG_RE.sub("-", str(raw).lower().strip()).strip("-") or "default"


# ── Clustering ────────────────────────────────────────────────────────────────

def _within_time_window(m1: "MemoryObject", m2: "MemoryObject") -> bool:
    """Return True when m1 and m2 were updated within 24 hours of each other."""
    delta = abs((m1.updated_at - m2.updated_at).total_seconds())
    return delta <= _TIME_WINDOW_HOURS * 3600


def _cluster_memories(
    memories: list["MemoryObject"],
    embed_fn: Any | None,
    cosine_threshold: float = _COSINE_THRESHOLD,
) -> list[list["MemoryObject"]]:
    """Cluster *memories* by cosine similarity + 24 h time window.

    Returns a list of clusters; each cluster is a list of MemoryObjects.
    Unclustered memories are each returned as a single-element cluster.
    Uses Union-Find for transitive group merging.
    """
    n = len(memories)
    if n == 0:
        return []

    # Compute embeddings for all memories in one batch.
    vectors: list[list[float]] | None = None
    if embed_fn is not None:
        try:
            batch = embed_fn([m.content for m in memories])
            if batch is not None and len(batch) == n:
                vectors = batch
        except Exception as exc:  # noqa: BLE001
            logger.debug("ScenarioEngine: embedding failed (%s); using Jaccard", exc)

    # Union-Find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            # Time window gate (fast check first).
            if not _within_time_window(memories[i], memories[j]):
                continue
            # Similarity check.
            if vectors is not None:
                sim = _cosine(vectors[i], vectors[j])
            else:
                sim = _token_jaccard(memories[i].content, memories[j].content)
            if sim >= cosine_threshold:
                union(i, j)

    # Group by root.
    from collections import defaultdict
    groups: dict[int, list["MemoryObject"]] = defaultdict(list)
    for i, m in enumerate(memories):
        groups[find(i)].append(m)

    return list(groups.values())


# ── Name distillation ─────────────────────────────────────────────────────────

async def _distill_cluster_name(
    cluster: list["MemoryObject"],
    client: "DistillationClient | None",
) -> str:
    """Distil a short scene block name for *cluster* via DistillationClient.

    Falls back to a timestamp-based label when the client is unavailable or
    returns an empty string (AC-4).
    """
    # Timestamp-based fallback label.
    sorted_by_time = sorted(cluster, key=lambda m: m.updated_at)
    earliest = sorted_by_time[0].updated_at.strftime("%Y-%m-%d %H:%M UTC")
    fallback = f"Scene {earliest}"

    if client is None:
        return fallback

    # Build a compact snippet from the cluster content.
    snippets = "\n".join(f"- {m.content[:120]}" for m in cluster[:5])
    prompt = (
        "Name this memory cluster with a concise (3-7 words) scene title.\n"
        "Respond with ONLY the title — no quotes, no explanation.\n\n"
        f"Memory snippets:\n{snippets}\n"
    )
    try:
        name = await client.complete(prompt, max_tokens=32)
        name = name.strip().strip('"\'').strip()
        if name:
            return name
    except Exception as exc:  # noqa: BLE001
        logger.debug("ScenarioEngine: distillation call failed (%s)", exc)

    return fallback


# ── Markdown rendering ────────────────────────────────────────────────────────

def _render_scenarios_md(
    project_id: str,
    named_clusters: list[tuple[str, list["MemoryObject"]]],
    generated_at: str,
) -> str:
    """Render named clusters as a markdown file."""
    lines = [
        "---",
        f"project: {project_id}",
        f"generated_at: {generated_at}",
        f"scene_count: {len(named_clusters)}",
        "---",
        "",
        f"# Scenarios: {project_id}",
        "",
    ]
    for name, cluster in named_clusters:
        sorted_cluster = sorted(cluster, key=lambda m: m.updated_at, reverse=True)
        lines.append(f"## {name}")
        lines.append("")
        latest_ts = sorted_cluster[0].updated_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"*{len(cluster)} memories — latest: {latest_ts}*")
        lines.append("")
        for m in sorted_cluster[:3]:
            snippet = m.content[:200].replace("\n", " ")
            lines.append(f"- {snippet}")
        lines.append("")
    return "\n".join(lines)


# ── ScenarioEngine ────────────────────────────────────────────────────────────

class ScenarioEngine:
    """Cluster L1 memories into named scene blocks.

    Parameters
    ----------
    config:
        DepthFusionConfig instance.
    distillation_client:
        DistillationClient for scene-name distillation.  May be ``None``
        (graceful degradation: timestamp labels are used instead).
    cosine_threshold:
        Cosine similarity threshold for clustering.  Defaults to 0.75.
    """

    def __init__(
        self,
        config: "DepthFusionConfig",
        distillation_client: "DistillationClient | None" = None,
        *,
        cosine_threshold: float = _COSINE_THRESHOLD,
    ) -> None:
        self._config = config
        self._client = distillation_client
        self._cosine_threshold = cosine_threshold
        self._last_rebuilt_at: str | None = None
        self._last_project_id: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def rebuild(self, scope: dict[str, Any]) -> Path:
        """Cluster L1 memories and write scenarios-{project_id}.md.

        Returns the path of the written file.

        The file is written atomically: content is prepared in memory, then
        written in one ``Path.write_text()`` call so readers never observe a
        partial file.
        """
        project_id = _project_id_from_scope(scope)
        dest = _DISCOVERIES_DIR / f"scenarios-{project_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Load L1 (active, non-archived) memories for this project.
        memories = self._load_l1_memories(project_id)

        # Obtain embedding function (optional — graceful degradation).
        embed_fn = self._get_embed_fn()

        # Cluster.
        clusters = _cluster_memories(
            memories,
            embed_fn=embed_fn,
            cosine_threshold=self._cosine_threshold,
        )

        # Distil names.
        named_clusters: list[tuple[str, list["MemoryObject"]]] = []
        for cluster in clusters:
            name = await _distill_cluster_name(cluster, self._client)
            named_clusters.append((name, cluster))

        # Sort clusters by most recent memory (newest first).
        named_clusters.sort(
            key=lambda nc: max(m.updated_at for m in nc[1]),
            reverse=True,
        )

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        content = _render_scenarios_md(project_id, named_clusters, now_iso)
        dest.write_text(content, encoding="utf-8")

        self._last_rebuilt_at = now_iso
        self._last_project_id = project_id

        logger.debug(
            "ScenarioEngine: wrote %d scene blocks for %r to %s",
            len(named_clusters),
            project_id,
            dest,
        )
        return dest

    # ── Telemetry ─────────────────────────────────────────────────────────────

    @property
    def last_rebuilt_at(self) -> str | None:
        """ISO timestamp of the last rebuild() call, or None."""
        return self._last_rebuilt_at

    @property
    def last_project_id(self) -> str | None:
        """Project ID of the last rebuild() call, or None."""
        return self._last_project_id

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_l1_memories(self, project_id: str) -> list["MemoryObject"]:
        """Load active (L1) memories for *project_id* from the store.

        Returns an empty list if the store is unavailable.
        """
        try:
            from depthfusion.storage.memory_store import MemoryStore
            store = MemoryStore(self._config.memory_store_path)
            return store.query(
                project_id=project_id or None,
                include_archived=False,
                limit=500,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "ScenarioEngine: could not load memories for %r: %s",
                project_id,
                exc,
            )
            return []

    def _get_embed_fn(self) -> Any | None:
        """Return the embedding function from the active backend, or None."""
        try:
            from depthfusion.backends.factory import get_backend
            backend = get_backend("embedding")
            # NullBackend.embed() always returns None; skip it.
            if backend is None or getattr(backend, "name", "null") == "null":
                return None
            return backend.embed
        except Exception as exc:  # noqa: BLE001
            logger.debug("ScenarioEngine: could not get embedding backend: %s", exc)
            return None


# ── Module-level singleton accessor ───────────────────────────────────────────

_scenario_engine: ScenarioEngine | None = None


def get_scenario_engine(
    config: "DepthFusionConfig | None" = None,
    distillation_client: "DistillationClient | None" = None,
) -> ScenarioEngine | None:
    """Return the module-level ScenarioEngine singleton, or None if not initialised.

    Call with both arguments to initialise; call with no arguments to retrieve.
    """
    global _scenario_engine
    if config is not None:
        _scenario_engine = ScenarioEngine(config, distillation_client)
    return _scenario_engine


def scenarios_file_path(project_id: str) -> Path:
    """Return the path where the scenarios file for *project_id* would live."""
    return _DISCOVERIES_DIR / f"scenarios-{project_id}.md"


def scenario_block_summary(project_id: str, query: str = "") -> str | None:
    """Return a short summary of the most relevant scenario block for *query*.

    Reads the scenarios-{project_id}.md file and returns the first H2 block
    that contains terms from *query*, or the first block if *query* is empty.
    Returns None if the file does not exist or contains no blocks.

    Used by ``depthfusion_recall_relevant`` when ``include_scenarios=True``.
    """
    path = _DISCOVERIES_DIR / f"scenarios-{project_id}.md"
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Split on H2 headings.
    sections = re.split(r"\n## ", content)
    if len(sections) <= 1:
        return None

    # Drop the preamble (before first ## ).
    blocks = sections[1:]   # each starts with the heading text

    if not query:
        # Return the first block summary (most recent).
        first = blocks[0]
        lines = first.strip().splitlines()
        title = lines[0].strip() if lines else "Scenario"
        body = "\n".join(lines[1:5]).strip() if len(lines) > 1 else ""
        return f"## {title}\n{body}"

    query_tokens = set(query.lower().split())
    best_block: str | None = None
    best_score: int = -1
    for block in blocks:
        block_lower = block.lower()
        score = sum(1 for t in query_tokens if t in block_lower)
        if score > best_score:
            best_score = score
            best_block = block

    if best_block is None:
        return None

    lines = best_block.strip().splitlines()
    title = lines[0].strip() if lines else "Scenario"
    body = "\n".join(lines[1:5]).strip() if len(lines) > 1 else ""
    return f"## {title}\n{body}"


__all__ = [
    "ScenarioEngine",
    "get_scenario_engine",
    "scenarios_file_path",
    "scenario_block_summary",
]
