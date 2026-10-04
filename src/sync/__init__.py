"""Cloud Sync subsystem.

Provides provider-abstraction interfaces for synchronizing watchlists,
alerts, settings, portfolio, strategies, and reports with an external
backing store.  No specific cloud provider is hardcoded — implement
:class:`SyncProvider` to plug in any backend.
"""

from __future__ import annotations

from src.sync.base import (
    SyncItem,
    SyncProvider,
    SyncResult,
    SyncStatus,
    default_sync_manager,
)
from src.sync.local import LocalSyncProvider
from src.sync.manager import SyncManager

__all__ = [
    "SyncItem",
    "SyncProvider",
    "SyncResult",
    "SyncStatus",
    "default_sync_manager",
    "LocalSyncProvider",
    "SyncManager",
]
