"""Cloud Sync — provider abstraction interfaces.

Defines the contracts for synchronizing application data with an
external backing store.  Providers are pluggable; no cloud provider is
hardcoded.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("nepse.sync")


class SyncStatus(str, Enum):
    """Result status of a sync operation."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class SyncKind(str, Enum):
    """Types of data that can be synchronized."""

    WATCHLIST = "watchlist"
    ALERTS = "alerts"
    SETTINGS = "settings"
    PORTFOLIO = "portfolio"
    STRATEGIES = "strategies"
    REPORTS = "reports"


@dataclass
class SyncItem:
    """A single data item to synchronize.

    Attributes:
        kind: Data category.
        key: Stable identifier within the category.
        payload: Serialisable payload.
        updated_at: ISO timestamp of last modification.
    """

    kind: SyncKind | str
    key: str
    payload: Any = None
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "kind": self.kind.value if isinstance(self.kind, SyncKind) else str(self.kind),
            "key": self.key,
            "payload": self.payload,
            "updated_at": self.updated_at,
        }


@dataclass
class SyncResult:
    """Outcome of a sync operation.

    Attributes:
        status: Overall status.
        message: Human-readable outcome.
        synced_items: Count of items synced.
        failed_items: Count of failed items.
        details: Per-kind breakdown.
    """

    status: SyncStatus = SyncStatus.SUCCESS
    message: str = ""
    synced_items: int = 0
    failed_items: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "status": self.status.value,
            "message": self.message,
            "synced_items": self.synced_items,
            "failed_items": self.failed_items,
            "details": self.details,
        }


class SyncProvider(ABC):
    """Abstract cloud/local sync backend.

    Implementations persist and retrieve :class:`SyncItem` payloads.
    """

    name: str = "provider"

    @abstractmethod
    def push(self, items: list[SyncItem]) -> SyncResult:
        """Push items to the backing store.

        Args:
            items: Items to upload.

        Returns:
            A :class:`SyncResult`.
        """
        raise NotImplementedError

    @abstractmethod
    def pull(
        self,
        kind: SyncKind | str | None = None,
    ) -> list[SyncItem]:
        """Pull items from the backing store.

        Args:
            kind: Optional filter by data category.

        Returns:
            List of stored items.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, kind: SyncKind | str, key: str) -> bool:
        """Delete an item from the backing store.

        Args:
            kind: Data category.
            key: Item key.

        Returns:
            True when removed.
        """
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        """Return provider metadata."""
        return {"name": self.name, "kind": "abstract"}


class SyncManager:
    """Aggregates multiple data categories through a provider."""

    def __init__(self, provider: SyncProvider) -> None:
        """Initialise the manager.

        Args:
            provider: The backing sync provider.
        """
        self._provider = provider

    @property
    def provider(self) -> SyncProvider:
        """Return the active provider."""
        return self._provider

    def push_items(self, items: list[SyncItem]) -> SyncResult:
        """Push a batch of items."""
        return self._provider.push(items)

    def pull_items(self, kind: SyncKind | str | None = None) -> list[SyncItem]:
        """Pull items, optionally filtered by kind."""
        return self._provider.pull(kind)


_default_manager: SyncManager | None = None


def default_sync_manager() -> SyncManager:
    """Return a lazily-created default sync manager.

    Uses :class:`LocalSyncProvider` (JSON files) when no other provider
    is configured.

    Returns:
        The default :class:`SyncManager`.
    """
    global _default_manager
    if _default_manager is None:
        from src.sync.local import LocalSyncProvider

        _default_manager = SyncManager(LocalSyncProvider())
    return _default_manager


def set_default_provider(provider: SyncProvider) -> None:
    """Replace the default sync provider.

    Args:
        provider: The new provider instance.
    """
    global _default_manager
    _default_manager = SyncManager(provider)
