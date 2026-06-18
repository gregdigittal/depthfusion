"""Idle-time opportunistic prefetch scheduler (E-58 S-189 T-655).

Selects candidate records for offline caching when the device is online and
idle.  The scheduler honours:

* A configurable byte budget (default 2 GB).
* A score-ordered candidate list supplied by the relevance scorer (T-653)
  or a simple recency fallback.
* Pinned projects / folders that must be force-included regardless of score.

Design rules
------------
* **No network calls here** — the scheduler emits a *plan* (a list of
  ``PrefetchCandidate`` items); the caller is responsible for fetching the
  data.  This keeps the scheduler unit-testable without network access.
* **Pinned items first** — pinned paths / projects are always included up to
  the budget; remaining budget is filled with score-ordered candidates.
* **Budget enforcement** — candidates are admitted until ``budget_bytes`` is
  exhausted.  Oversized single items are skipped (not truncated).
* **Idle detection** — the scheduler does not implement idle detection itself;
  the caller is expected to gate invocation on an appropriate idle signal.
  ``is_idle()`` is provided as a stub that callers may override in tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

__all__ = [
    "PrefetchCandidate",
    "PrefetchPlan",
    "PrefetchScheduler",
]

_DEFAULT_BUDGET_BYTES: int = 2 * 1024 ** 3  # 2 GB


@dataclass(order=False)
class PrefetchCandidate:
    """A record that the scheduler has selected for prefetch.

    Attributes
    ----------
    path:
        Logical path or record identifier.
    score:
        Relevance score (higher is more important).
    size_bytes:
        Estimated size of the record payload in bytes.
    pinned:
        Whether this candidate was force-included due to a pin.
    project:
        Optional project the record belongs to.
    """

    path: str
    score: float = 0.0
    size_bytes: int = 0
    pinned: bool = False
    project: Optional[str] = None


@dataclass
class PrefetchPlan:
    """The output of a scheduler run.

    Attributes
    ----------
    selected:
        Candidates admitted within the budget (pinned first, then scored).
    skipped_over_budget:
        Candidates that were evaluated but skipped because the budget was full.
    budget_bytes:
        The total byte budget used for this run.
    budget_used_bytes:
        Sum of ``size_bytes`` for all selected candidates.
    generated_at:
        Unix timestamp when the plan was generated.
    """

    selected: list[PrefetchCandidate] = field(default_factory=list)
    skipped_over_budget: list[PrefetchCandidate] = field(default_factory=list)
    budget_bytes: int = _DEFAULT_BUDGET_BYTES
    budget_used_bytes: int = 0
    generated_at: float = field(default_factory=time.time)

    @property
    def pinned_count(self) -> int:
        """Number of force-included (pinned) candidates in the plan."""
        return sum(1 for c in self.selected if c.pinned)

    @property
    def scored_count(self) -> int:
        """Number of score-ranked (non-pinned) candidates in the plan."""
        return sum(1 for c in self.selected if not c.pinned)


class PrefetchScheduler:
    """Selects records for offline prefetching when online + idle.

    Parameters
    ----------
    budget_bytes:
        Maximum total payload size to prefetch in one run.
    pinned_projects:
        Project names whose records must be force-included.
    pinned_paths:
        Individual record paths to force-include regardless of score.
    idle_fn:
        Callable returning ``True`` when the system is considered idle.
        Defaults to always-True (useful for testing).
    """

    def __init__(
        self,
        budget_bytes: int = _DEFAULT_BUDGET_BYTES,
        pinned_projects: Optional[list[str]] = None,
        pinned_paths: Optional[list[str]] = None,
        idle_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._budget_bytes = budget_bytes
        self._pinned_projects: frozenset[str] = frozenset(pinned_projects or [])
        self._pinned_paths: frozenset[str] = frozenset(pinned_paths or [])
        self._idle_fn: Callable[[], bool] = idle_fn or (lambda: True)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def is_idle(self) -> bool:
        """Return True if the system is currently idle.

        Delegates to the ``idle_fn`` supplied at construction time.
        Callers should gate ``build_plan()`` on this returning True.
        """
        return self._idle_fn()

    def build_plan(
        self,
        candidates: list[PrefetchCandidate],
        now: Optional[float] = None,
    ) -> PrefetchPlan:
        """Build a prefetch plan from the given candidates.

        The plan admits:
        1. Pinned items (by path or project) — force-included first, in
           score-descending order within the pinned set.
        2. Non-pinned items — admitted in score-descending order until the
           budget is exhausted.

        Candidates whose ``size_bytes`` alone would exceed the remaining
        budget are skipped (not truncated).

        Parameters
        ----------
        candidates:
            All candidate records (from the relevance scorer or a fallback
            list).  Items need not be pre-sorted.
        now:
            Override the plan timestamp (useful for deterministic testing).
        """
        plan_ts = now if now is not None else time.time()
        budget_remaining = self._budget_bytes

        # Separate pinned from non-pinned
        pinned_candidates: list[PrefetchCandidate] = []
        regular_candidates: list[PrefetchCandidate] = []

        for c in candidates:
            if self._is_pinned(c):
                # Mark as pinned so downstream knows
                c = PrefetchCandidate(
                    path=c.path,
                    score=c.score,
                    size_bytes=c.size_bytes,
                    pinned=True,
                    project=c.project,
                )
                pinned_candidates.append(c)
            else:
                regular_candidates.append(c)

        # Sort both groups by score descending
        pinned_candidates.sort(key=lambda c: c.score, reverse=True)
        regular_candidates.sort(key=lambda c: c.score, reverse=True)

        selected: list[PrefetchCandidate] = []
        skipped: list[PrefetchCandidate] = []

        # Admit pinned first
        for c in pinned_candidates:
            if c.size_bytes <= budget_remaining:
                selected.append(c)
                budget_remaining -= c.size_bytes
            else:
                skipped.append(c)

        # Then admit scored candidates
        for c in regular_candidates:
            if c.size_bytes <= budget_remaining:
                selected.append(c)
                budget_remaining -= c.size_bytes
            else:
                skipped.append(c)

        budget_used = self._budget_bytes - budget_remaining

        return PrefetchPlan(
            selected=selected,
            skipped_over_budget=skipped,
            budget_bytes=self._budget_bytes,
            budget_used_bytes=budget_used,
            generated_at=plan_ts,
        )

    def add_pin(self, path: str) -> None:
        """Add a path to the pinned set."""
        self._pinned_paths = self._pinned_paths | {path}

    def add_pinned_project(self, project: str) -> None:
        """Add a project to the pinned project set."""
        self._pinned_projects = self._pinned_projects | {project}

    def remove_pin(self, path: str) -> None:
        """Remove a path from the pinned set."""
        self._pinned_paths = self._pinned_paths - {path}

    def remove_pinned_project(self, project: str) -> None:
        """Remove a project from the pinned project set."""
        self._pinned_projects = self._pinned_projects - {project}

    @property
    def pinned_paths(self) -> frozenset[str]:
        """Currently pinned paths."""
        return self._pinned_paths

    @property
    def pinned_projects(self) -> frozenset[str]:
        """Currently pinned projects."""
        return self._pinned_projects

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _is_pinned(self, c: PrefetchCandidate) -> bool:
        """Return True if *c* should be force-included."""
        if c.path in self._pinned_paths:
            return True
        if c.project is not None and c.project in self._pinned_projects:
            return True
        return False
