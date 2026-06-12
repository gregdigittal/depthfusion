"""SharePoint sync scheduler with file-based locking (T-617).

SyncLock
    Prevents concurrent SharePoint sync processes using a PID-stamped lock
    file at ~/.depthfusion/sharepoint_sync.lock.

schedule_sync
    Convenience function: acquires the lock, iterates over a list of
    SiteScope entries, and calls connector.sync_incremental() for each.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from depthfusion.connectors.sharepoint import SharePointConnector
    from depthfusion.connectors.sharepoint_scope import SiteScope


_DEFAULT_LOCK_PATH = Path.home() / ".depthfusion" / "sharepoint_sync.lock"


class SyncLock:
    """File-based process lock for SharePoint sync jobs.

    The lock file stores the PID of the owning process.  If the lock file
    exists but the recorded PID is no longer alive (stale lock), the lock is
    stolen automatically.

    Usage::

        with SyncLock() as lock:
            ...  # exclusive sync work

    Raises:
        RuntimeError: If the lock is held by another *live* process.
    """

    def __init__(self, lock_path: Path | None = None) -> None:
        self._path = lock_path or _DEFAULT_LOCK_PATH

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "SyncLock":
        self._acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self._release()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _acquire(self) -> None:
        """Try to acquire the lock; raise RuntimeError if a live process owns it."""
        if self._path.exists():
            try:
                pid_str = self._path.read_text(encoding="utf-8").strip()
                pid = int(pid_str)
            except (ValueError, OSError):
                # Corrupt lock file — steal it.
                pid = 0

            if pid and self._pid_alive(pid):
                raise RuntimeError(
                    f"SharePoint sync already running (PID {pid})"
                )
            # Stale lock — fall through and overwrite.

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(str(os.getpid()), encoding="utf-8")

    def _release(self) -> None:
        """Remove the lock file, ignoring errors (e.g., already removed)."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return ``True`` if *pid* refers to a running process."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            # ProcessLookupError → no such process
            # PermissionError   → process exists but we can't signal it
            return False
        except OSError:
            return False


def schedule_sync(
    sites: "list[SiteScope]",
    connector: "SharePointConnector",
) -> None:
    """Acquire the sync lock, then sync each *enabled* site incrementally.

    Args:
        sites:      List of :class:`~depthfusion.connectors.sharepoint_scope.SiteScope`
                    entries to process.
        connector:  Configured :class:`~depthfusion.connectors.sharepoint.SharePointConnector`
                    instance.

    Raises:
        RuntimeError: If the sync lock is held by another live process.
    """
    with SyncLock():
        for site in sites:
            if not site.enabled:
                continue

            connector._emit_telemetry(
                "schedule_sync_start",
                {"site_url": site.site_url, "drive_id": site.drive_id},
            )
            try:
                docs, _token = connector.sync_incremental(
                    site_url=site.site_url,
                    drive_id=site.drive_id,
                )
                connector._emit_telemetry(
                    "schedule_sync_complete",
                    {
                        "site_url": site.site_url,
                        "drive_id": site.drive_id,
                        "count": len(docs),
                    },
                )
            except Exception as exc:
                connector._emit_telemetry(
                    "schedule_sync_error",
                    {
                        "site_url": site.site_url,
                        "drive_id": site.drive_id,
                        "error": str(exc),
                    },
                )
                raise


__all__ = ["SyncLock", "schedule_sync"]
