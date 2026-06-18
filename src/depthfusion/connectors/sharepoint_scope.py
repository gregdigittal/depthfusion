"""SharePoint site scope management (T-605).

SiteScope
    Dataclass describing one SharePoint site + drive to sync.

SiteScopeStore
    Persists a list of SiteScope entries to ~/.depthfusion/sharepoint_sites.json.

CLI usage::

    python -m depthfusion.connectors.sharepoint_scope add \\
        --site-url URL --drive-id ID [--label LABEL]
    python -m depthfusion.connectors.sharepoint_scope remove --site-url URL
    python -m depthfusion.connectors.sharepoint_scope list
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

_DEFAULT_STORE_PATH = Path.home() / ".depthfusion" / "sharepoint_sites.json"


@dataclass
class SiteScope:
    """One SharePoint site/drive combination to include in syncs.

    Attributes:
        site_url:  Full HTTPS URL of the SharePoint site.
        drive_id:  Microsoft Graph drive identifier (``b!...``).
        label:     Optional human-readable label for display purposes.
        enabled:   When ``False`` the site is skipped during scheduled syncs.
    """

    site_url: str
    drive_id: str
    label: str = ""
    enabled: bool = True


class SiteScopeStore:
    """Persist a list of :class:`SiteScope` entries to a JSON file.

    Args:
        store_path: Path to the JSON file.  Defaults to
                    ``~/.depthfusion/sharepoint_sites.json``.
    """

    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path or _DEFAULT_STORE_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, scope: SiteScope) -> None:
        """Add or replace a scope entry.

        If an entry with the same ``site_url`` already exists it is replaced;
        otherwise the new entry is appended.
        """
        scopes = self.list()
        for i, existing in enumerate(scopes):
            if existing.site_url == scope.site_url:
                scopes[i] = scope
                self._save(scopes)
                return
        scopes.append(scope)
        self._save(scopes)

    def remove(self, site_url: str) -> None:
        """Remove the entry with *site_url* (no-op if not found)."""
        scopes = self.list()
        filtered = [s for s in scopes if s.site_url != site_url]
        if len(filtered) != len(scopes):
            self._save(filtered)

    def list(self) -> List[SiteScope]:
        """Return all stored :class:`SiteScope` entries."""
        return self._load()

    def get(self, site_url: str) -> SiteScope | None:
        """Return the entry for *site_url*, or ``None`` if not found."""
        for scope in self.list():
            if scope.site_url == site_url:
                return scope
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> List[SiteScope]:
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
            raw = json.loads(text)
            if isinstance(raw, list):
                return [
                    SiteScope(
                        site_url=entry.get("site_url", ""),
                        drive_id=entry.get("drive_id", ""),
                        label=entry.get("label", ""),
                        enabled=entry.get("enabled", True),
                    )
                    for entry in raw
                    if isinstance(entry, dict)
                ]
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self, scopes: List[SiteScope]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([asdict(s) for s in scopes], indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m depthfusion.connectors.sharepoint_scope",
        description="Manage SharePoint site scopes for DepthFusion syncs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    add_p = sub.add_parser("add", help="Register a site/drive for syncing.")
    add_p.add_argument("--site-url", required=True, help="SharePoint site URL.")
    add_p.add_argument("--drive-id", required=True, help="Graph drive ID.")
    add_p.add_argument("--label", default="", help="Human-readable label.")
    add_p.add_argument(
        "--disabled",
        action="store_true",
        help="Register the site but mark it disabled.",
    )

    # remove
    rm_p = sub.add_parser("remove", help="Un-register a site.")
    rm_p.add_argument("--site-url", required=True, help="SharePoint site URL.")

    # list
    sub.add_parser("list", help="List all registered sites.")

    args = parser.parse_args()
    store = SiteScopeStore()

    if args.command == "add":
        scope = SiteScope(
            site_url=args.site_url,
            drive_id=args.drive_id,
            label=args.label,
            enabled=not args.disabled,
        )
        store.add(scope)
        print(f"Added: {args.site_url} (drive={args.drive_id})")

    elif args.command == "remove":
        store.remove(args.site_url)
        print(f"Removed: {args.site_url}")

    elif args.command == "list":
        scopes = store.list()
        if not scopes:
            print("No sites registered.")
        else:
            for s in scopes:
                status = "enabled" if s.enabled else "disabled"
                label = f"  [{s.label}]" if s.label else ""
                print(f"{s.site_url}  drive={s.drive_id}  {status}{label}")

    sys.exit(0)


__all__ = ["SiteScope", "SiteScopeStore"]
