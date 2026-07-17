"""ContextOffloader — write large text blobs to refs/ and return Mermaid node refs.

E-68 S-231.

Design:
- offload(text, session_id) → str
    1. Generates a short node_id (sha256 prefix of text).
    2. Writes text to ~/.claude/shared/refs/{session_id}/{node_id}.md
    3. Returns a compact Mermaid node reference string:
       ``ref[/"📎 ctx:{node_id}"/]``

- retrieve(node_id, session_id) → str
    Reads and returns the raw text from refs/{session_id}/{node_id}.md.
    Raises FileNotFoundError if not present.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from depthfusion.core.config import DepthFusionConfig

logger = logging.getLogger(__name__)

_REFS_BASE = Path.home() / ".claude" / "shared" / "refs"

# Mermaid node-ref template — kept compact so it embeds cleanly in a canvas.
_MMD_REF_TEMPLATE = 'ref_{node_id}[/"📎 ctx:{node_id}"/]'


class ContextOffloader:
    """Offload large context blobs to disk; return Mermaid node references.

    Parameters
    ----------
    config:
        DepthFusionConfig instance. Reads ``offload_enabled`` and
        ``offload_mmd_max_tokens`` fields.

    Usage
    -----
    offloader = ContextOffloader(config)
    mmd_ref = offloader.offload(long_text, session_id="abc123")
    # → 'ref_a1b2c3d4[/"📎 ctx:a1b2c3d4"/]'

    raw = offloader.retrieve("a1b2c3d4", session_id="abc123")
    """

    def __init__(self, config: "DepthFusionConfig") -> None:
        self._config = config
        self._refs_base = _REFS_BASE

    # ── Public API ─────────────────────────────────────────────────────────────

    def offload(self, text: str, session_id: str) -> str:
        """Write *text* to refs/{session_id}/{node_id}.md and return a Mermaid ref.

        Parameters
        ----------
        text:
            The text to offload.
        session_id:
            Identifier for the current session; used as a subdirectory name.

        Returns
        -------
        str
            A compact Mermaid node-reference string embedding the node_id.
        """
        node_id = self._node_id(text)
        out_dir = self._refs_base / session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{node_id}.md"
        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning("[offloader] failed to write ref %s: %s", out_path, exc)
            raise
        logger.debug("[offloader] offloaded node %s → %s", node_id, out_path)
        return _MMD_REF_TEMPLATE.format(node_id=node_id)

    def retrieve(self, node_id: str, session_id: str) -> str:
        """Read and return raw offloaded text for *node_id* / *session_id*.

        Raises
        ------
        FileNotFoundError
            If the refs file does not exist.
        """
        ref_path = self._refs_base / session_id / f"{node_id}.md"
        if not ref_path.exists():
            raise FileNotFoundError(
                f"No offloaded ref for node_id={node_id!r} session_id={session_id!r}"
            )
        return ref_path.read_text(encoding="utf-8")

    def refs_count(self, session_id: str | None = None) -> int:
        """Return the number of stored ref files.

        If *session_id* is given, counts only that session's refs.
        Otherwise counts all refs under the base directory.
        """
        base = self._refs_base / session_id if session_id else self._refs_base
        if not base.exists():
            return 0
        return sum(1 for _ in base.rglob("*.md"))

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _node_id(text: str) -> str:
        """Return an 8-hex-char node ID derived from the SHA-256 of *text*."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def mmd_ref(node_id: str) -> str:
        """Return the Mermaid node-reference string for a known *node_id*."""
        return _MMD_REF_TEMPLATE.format(node_id=node_id)
