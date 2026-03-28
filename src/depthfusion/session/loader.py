"""Session loader — loads SessionBlocks from .tmp + .meta.yaml session files."""
from __future__ import annotations

from pathlib import Path

import yaml

from depthfusion.core.types import SessionBlock
from depthfusion.session.scorer import SessionScorer


class SessionLoader:
    """Loads session blocks from a sessions directory.

    Uses .meta.yaml sidecar files for project/tag metadata when available.
    """

    def __init__(self, sessions_dir: Path | None = None, top_k: int = 5) -> None:
        self._sessions_dir = sessions_dir
        self._top_k = top_k
        self._scorer = SessionScorer()

    def load_relevant(self, task_description: str) -> list[SessionBlock]:
        """Load top_k most relevant session blocks for task_description."""
        all_blocks = self.load_all()
        if not all_blocks:
            return []

        scored = self._scorer.score_blocks(all_blocks, task_description)
        return [block for block, _ in scored[: self._top_k]]

    def load_all(self) -> list[SessionBlock]:
        """Load all session blocks from all .tmp files in the sessions directory."""
        if self._sessions_dir is None or not self._sessions_dir.exists():
            return []

        blocks: list[SessionBlock] = []
        for tmp_file in sorted(self._sessions_dir.glob("*.tmp")):
            block = self._load_single(tmp_file)
            if block is not None:
                blocks.append(block)

        return blocks

    def load_by_project(self, project: str) -> list[SessionBlock]:
        """Load blocks from sessions tagged with the given project."""
        all_blocks = self.load_all()
        return [b for b in all_blocks if project in b.tags]

    def _load_single(self, tmp_file: Path) -> SessionBlock | None:
        """Load a single .tmp file, enriching with .meta.yaml if present."""
        try:
            content = tmp_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        session_id = tmp_file.stem
        tags: list[str] = []

        # Load sidecar metadata if available
        sidecar = tmp_file.with_suffix(".meta.yaml")
        if sidecar.exists():
            try:
                with sidecar.open(encoding="utf-8") as f:
                    meta = yaml.safe_load(f)
                if isinstance(meta, dict):
                    # Build tags from project, category, and keywords
                    project = meta.get("project", "unknown")
                    category = meta.get("category", "other")
                    keywords = meta.get("keywords") or []
                    tags = [project, category] + list(keywords)
            except (yaml.YAMLError, OSError):
                pass  # Fall through with empty tags

        return SessionBlock(
            session_id=session_id,
            block_index=0,
            content=content,
            tags=tags,
        )
