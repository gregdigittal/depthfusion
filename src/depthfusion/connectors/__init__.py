"""depthfusion.connectors — External document source connectors (E-54+).

Available connectors:

* :mod:`~depthfusion.connectors.sharepoint` — Microsoft Graph / SharePoint
* :mod:`~depthfusion.connectors.sharepoint_state` — Delta cursor store + applicator
* :mod:`~depthfusion.connectors.sharepoint_scope` — Site scope management
* :mod:`~depthfusion.connectors.sharepoint_scheduler` — Scheduler + file-based lock

Usage::

    from depthfusion.connectors.sharepoint import SharePointConnector
    from depthfusion.connectors.sharepoint_state import DeltaCursorStore, DeltaApplicator
    from depthfusion.connectors.sharepoint_scope import SiteScope, SiteScopeStore
    from depthfusion.connectors.sharepoint_scheduler import SyncLock, schedule_sync
"""
from __future__ import annotations

__all__: list[str] = []
