"""PersonaEngine — project persona synthesis from memory.

E-68 S-229.

Generates a markdown persona file that summarises the working style, preferences,
and common patterns observed across a project's memory corpus.  The file is written
to ``~/.claude/shared/discoveries/persona-{project_id}.md`` so that
``depthfusion_recall_relevant`` (with ``include_persona=True``) can prepend it as a
context preamble.

``maybe_trigger`` gates generation: it fires ``generate`` only when the
cumulative new-memory count crosses a multiple of
``config.persona_trigger_every_n``.  The count is NOT persisted across MCP
restarts — it resets on each server restart.  This is intentional: personas
are regenerated periodically rather than once per lifetime.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from depthfusion.cognitive.distillation_client import DistillationClient
    from depthfusion.core.config import DepthFusionConfig

logger = logging.getLogger(__name__)

_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"

# Regex to extract a clean slug from arbitrary project_id values (path-safe).
_SLUG_RE = re.compile(r"[^a-z0-9-]")


def _project_id_from_scope(scope: dict[str, Any]) -> str:
    """Derive a filesystem-safe project slug from a scope dict.

    Per clarification 8a the scope dict may contain:
      - ``project_id``   — used verbatim (after sanitisation) if present
      - ``project``      — fallback
      - ``slug``         — fallback
    Falls back to ``"default"`` when none are present.
    """
    raw = (
        scope.get("project_id")
        or scope.get("project")
        or scope.get("slug")
        or "default"
    )
    return _SLUG_RE.sub("-", str(raw).lower().strip()).strip("-") or "default"


class PersonaEngine:
    """Generate and cache a project persona from the distillation client.

    Parameters
    ----------
    config:
        DepthFusionConfig — reads ``persona_trigger_every_n``.
    distillation_client:
        DistillationClient instance for LLM completion.
    """

    def __init__(
        self,
        config: "DepthFusionConfig",
        distillation_client: "DistillationClient",
    ) -> None:
        self._config = config
        self._client = distillation_client
        # Track state for maybe_trigger
        self._last_trigger_count: int = 0
        self._persona_last_updated: str | None = None   # ISO timestamp
        self._memory_count_at_last_generation: int | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def generate(self, scope: dict[str, Any]) -> Path:
        """Generate the persona file for *scope* and write it to discoveries/.

        Returns the ``Path`` of the written file.

        The prompt asks the LLM to synthesise a short markdown document that
        describes the project's characteristic patterns — tech choices, coding
        style, decision tendencies, and recurring themes observed across the
        corpus.  When the LLM call fails or returns an empty string, a minimal
        placeholder file is written so callers never block.
        """
        project_id = _project_id_from_scope(scope)
        dest = _DISCOVERIES_DIR / f"persona-{project_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Build prompt from the scope and any existing discovery context.
        prompt = self._build_prompt(project_id, scope)

        content: str = ""
        try:
            content = await self._client.complete(prompt, max_tokens=800)
        except Exception as exc:  # noqa: BLE001 — graceful degradation
            logger.warning(
                "PersonaEngine: distillation client raised during generate "
                "for project %r: %s",
                project_id,
                exc,
            )

        if not content.strip():
            content = (
                f"# Persona: {project_id}\n\n"
                "_Persona generation pending — no LLM output was returned._\n"
            )

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        header = (
            f"---\nproject: {project_id}\n"
            f"generated_at: {now_iso}\n---\n\n"
        )
        dest.write_text(header + content, encoding="utf-8")

        # Update internal telemetry fields.
        self._persona_last_updated = now_iso
        # new_count is set by maybe_trigger; when called directly, record None.
        if self._memory_count_at_last_generation is None:
            self._memory_count_at_last_generation = 0

        logger.debug(
            "PersonaEngine: wrote persona for %r to %s", project_id, dest
        )

        # E-68 S-230 AC-2: trigger ScenarioEngine.rebuild() as a post-pass.
        await self._trigger_scenario_rebuild(scope)

        return dest

    async def _trigger_scenario_rebuild(self, scope: dict[str, Any]) -> None:
        """Trigger ScenarioEngine.rebuild() as a post-pass after generate().

        Imports lazily to avoid a circular dependency between persona and
        scenario modules.  Failures are swallowed — persona generation must
        never be blocked by scenario failures.
        """
        try:
            from depthfusion.cognitive.scenario import get_scenario_engine
            engine = get_scenario_engine()
            if engine is not None:
                await engine.rebuild(scope)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "PersonaEngine: scenario rebuild post-pass failed: %s", exc
            )

    def maybe_trigger(self, scope: dict[str, Any], new_count: int) -> bool:
        """Fire :py:meth:`generate` if *new_count* crossed a trigger boundary.

        The trigger fires when::

            floor(new_count / n) > floor(last_trigger_count / n)

        where ``n = config.persona_trigger_every_n``.

        This means the *first* call with ``new_count >= n`` triggers, then
        the next trigger fires at ``2*n``, etc.

        Returns ``True`` when generation was triggered, ``False`` otherwise.
        """
        n = self._config.persona_trigger_every_n
        if n <= 0:
            return False

        prev_bucket = self._last_trigger_count // n
        new_bucket = new_count // n

        if new_bucket <= prev_bucket:
            return False

        # Update before scheduling to avoid race on repeated calls.
        self._last_trigger_count = new_count
        self._memory_count_at_last_generation = new_count

        # Run in a new event loop when called from sync context; if an event
        # loop is already running (async context) we schedule a task instead.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.generate(scope))
        except RuntimeError:
            # No running event loop — run synchronously via asyncio.run().
            try:
                asyncio.run(self.generate(scope))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PersonaEngine.maybe_trigger: generate failed: %s", exc
                )

        return True

    # ── Telemetry (for _tool_status) ─────────────────────────────────────────

    @property
    def persona_last_updated(self) -> str | None:
        """ISO timestamp of the last successful generate(), or None."""
        return self._persona_last_updated

    @property
    def memory_count_at_last_generation(self) -> int | None:
        """Memory count at the time of the last generate() call, or None."""
        return self._memory_count_at_last_generation

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_prompt(self, project_id: str, scope: dict[str, Any]) -> str:
        """Construct the distillation prompt for persona generation."""
        extra_ctx = ""
        if scope:
            _skip = ("project_id", "project", "slug")
            lines = [f"  {k}: {v}" for k, v in scope.items() if k not in _skip]
            if lines:
                extra_ctx = "\nAdditional scope context:\n" + "\n".join(lines) + "\n"

        return (
            f"You are DepthFusion generating a project persona for '{project_id}'.\n"
            f"{extra_ctx}\n"
            "Based on the project's memory corpus, write a concise markdown document "
            "(under 600 words) that summarises:\n"
            "1. The project's primary technology stack and architectural patterns.\n"
            "2. Recurring coding style preferences and naming conventions.\n"
            "3. Common decision patterns (e.g., error-handling strategy, testing approach).\n"
            "4. Any notable recurring themes or pain points.\n\n"
            "Format as markdown with H2 sections. Be specific and factual — avoid "
            "generic advice. Use bullet points for lists.\n"
        )


# ── Module-level singleton accessor ────────────────────────────────────────────

_persona_engine: PersonaEngine | None = None


def get_persona_engine(
    config: "DepthFusionConfig | None" = None,
    distillation_client: "DistillationClient | None" = None,
) -> PersonaEngine | None:
    """Return the module-level PersonaEngine singleton, or None if not initialised.

    Call with both arguments to initialise; call with no arguments to retrieve.
    Returns None when the engine has not been initialised (e.g. when tests do
    not set it up, callers must handle None gracefully).
    """
    global _persona_engine
    if config is not None and distillation_client is not None:
        _persona_engine = PersonaEngine(config, distillation_client)
    return _persona_engine


def persona_file_path(project_id: str) -> Path:
    """Return the path where the persona file for *project_id* would live."""
    return _DISCOVERIES_DIR / f"persona-{project_id}.md"
