"""Admin CLI for device registry management.

Usage::

    depthfusion devices list
    depthfusion devices revoke <device_id>

The DB path is read from the ``DEPTHFUSION_DATA_DIR`` environment variable
(falling back to ``~/.depthfusion``).  The same ``identity.db`` file is shared
with :class:`~depthfusion.identity.principal_store.PrincipalStore`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from depthfusion.identity.device_registry import DeviceRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "identity.db"


def _registry(db_path: Path | None = None) -> DeviceRegistry:
    return DeviceRegistry(db_path or _default_db_path())


def _fmt_ts(ts: float) -> str:
    """Format a Unix timestamp as a human-readable UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------


def cmd_list(db_path: Path | None = None) -> int:
    """Print all registered devices to stdout.

    Returns
    -------
    int
        Exit code (0 = success, 1 = no devices found).
    """
    registry = _registry(db_path)
    records = registry.list_all()

    if not records:
        print("No devices registered.")
        return 1

    header = f"{'DEVICE_ID':<36}  {'OWNER':<36}  {'PLATFORM':<10}  {'LAST_SYNC':<25}  REVOKED"
    print(header)
    print("-" * len(header))
    for rec in records:
        revoked_label = "YES" if rec.revoked else "no"
        sync_str = _fmt_ts(rec.last_sync) if rec.last_sync else "never"
        print(
            f"{rec.device_id:<36}  "
            f"{rec.owner_principal_id:<36}  "
            f"{rec.platform:<10}  "
            f"{sync_str:<25}  "
            f"{revoked_label}"
        )
    return 0


def cmd_revoke(device_id: str, db_path: Path | None = None) -> int:
    """Revoke the device with *device_id*.

    Returns
    -------
    int
        Exit code (0 = revoked, 1 = not found).
    """
    registry = _registry(db_path)
    if registry.revoke(device_id):
        print(f"Device {device_id!r} revoked.")
        return 0
    else:
        print(f"Error: device {device_id!r} not found.", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the appropriate sub-command.

    Parameters
    ----------
    argv:
        Argument list (excluding the program name).  Defaults to
        :data:`sys.argv[1:]` when *None*.

    Returns
    -------
    int
        Exit code.
    """
    args = list(argv if argv is not None else sys.argv[1:])

    if not args or args[0] in ("-h", "--help"):
        print(
            "Usage:\n"
            "  depthfusion devices list\n"
            "  depthfusion devices revoke <device_id>\n"
        )
        return 0

    sub = args[0]

    if sub == "list":
        return cmd_list()
    elif sub == "revoke":
        if len(args) < 2:
            print("Error: 'revoke' requires a <device_id> argument.", file=sys.stderr)
            return 2
        return cmd_revoke(args[1])
    else:
        print(f"Error: unknown sub-command {sub!r}.", file=sys.stderr)
        print("Available: list, revoke", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["cmd_list", "cmd_revoke", "main"]
