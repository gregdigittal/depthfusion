"""SharePoint delta cursor persistence and delta-response applicator (T-608).

DeltaCursorStore
    Thread-safe JSON-backed store for per-drive delta tokens so incremental
    syncs can resume from where the last run left off.

DeltaApplicator
    Yields (action, item) tuples from a raw Microsoft Graph delta response.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Generator

_DEFAULT_STORE_PATH = Path.home() / ".depthfusion" / "sharepoint_cursors.json"


class DeltaCursorStore:
    """Persist delta tokens keyed by (tenant_id, site_url, drive_id).

    The key is an arbitrary string — callers are responsible for constructing
    a stable, unique key.  The recommended format is::

        f"{tenant_id}:{site_url}:{drive_id}"

    The backing file is created on first write.  All reads/writes are
    protected by a threading.Lock so the store is safe to use from multiple
    threads within the same process.

    Args:
        store_path: Path to the JSON file.  Defaults to
                    ``~/.depthfusion/sharepoint_cursors.json``.
    """

    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path or _DEFAULT_STORE_PATH
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_delta_token(self, key: str) -> str | None:
        """Return the stored delta token for *key*, or ``None`` if absent."""
        with self._lock:
            data = self._load()
            return data.get(key)

    def set_delta_token(self, key: str, token: str) -> None:
        """Persist *token* for *key*, overwriting any existing value."""
        with self._lock:
            data = self._load()
            data[key] = token
            self._save(data)

    def clear(self, key: str) -> None:
        """Remove the stored token for *key* (no-op if absent)."""
        with self._lock:
            data = self._load()
            if key in data:
                del data[key]
                self._save(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, str]:
        """Read and return the JSON store, or return an empty dict."""
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save(self, data: dict[str, str]) -> None:
        """Atomically write *data* to the backing file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class DeltaApplicator:
    """Classify items from a Graph delta response into add/update/delete actions.

    Usage::

        applicator = DeltaApplicator()
        for action, item in applicator.apply(delta_response):
            if action == "delete":
                handle_delete(item["id"])
            else:
                handle_upsert(item)

    The distinction between ``"add"`` and ``"update"`` is not surfaced by
    the Graph delta API itself (both appear as plain items without a
    ``"deleted"`` key).  Callers that need the distinction should track
    known item IDs and infer it from presence/absence.
    """

    def apply(
        self,
        delta_response: dict,
    ) -> Generator[tuple[str, dict], None, None]:
        """Yield ``(action, item)`` tuples for every entry in *delta_response*.

        Args:
            delta_response: The JSON-decoded body of a Graph delta query
                            response (must contain a ``"value"`` list).

        Yields:
            Tuples of ``(action, item)`` where *action* is one of
            ``"add"``, ``"update"``, or ``"delete"``.
        """
        for item in delta_response.get("value", []):
            if item.get("deleted"):
                yield "delete", item
            else:
                yield "add", item


__all__ = ["DeltaCursorStore", "DeltaApplicator"]
