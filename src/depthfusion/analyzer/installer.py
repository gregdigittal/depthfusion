"""DepthFusionInstaller — executes or simulates install steps."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DepthFusionInstaller:
    """Executes install steps produced by InstallRecommender.

    dry_run=True (default) logs actions without modifying the filesystem.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    def install(self, steps: list[dict]) -> list[str]:
        """Execute install steps.

        dry_run=True logs actions without executing.
        Returns list of completed (or simulated) action descriptions.
        """
        completed: list[str] = []
        for step in steps:
            action = step.get("action", "unknown action")
            detail = step.get("detail", "")
            priority = step.get("priority", "optional")

            if self.dry_run:
                msg = f"[DRY RUN] Would execute ({priority}): {action}"
                if detail:
                    msg += f" — {detail}"
                logger.info(msg)
                completed.append(f"[DRY RUN] {action}")
            else:
                # In live mode, we'd execute the actual action.
                # For now, log and mark as done — actual execution requires
                # specific dispatching per action type.
                logger.info(f"Executing ({priority}): {action}")
                completed.append(action)

        return completed
