"""Local sync provider — JSON-file backed storage.

A reference :class:`SyncProvider` implementation that stores items as
JSON files under a directory.  Useful as a fallback and for testing;
cloud providers can implement the same interface.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.sync.base import (
    SyncItem,
    SyncKind,
    SyncProvider,
    SyncResult,
    SyncStatus,
)

logger = logging.getLogger("nepse.sync.local")


class LocalSyncProvider(SyncProvider):
    """Filesystem-backed sync provider.

    Usage::

        provider = LocalSyncProvider(Path("sync_store"))
        provider.push([SyncItem(kind="watchlist", key="default", payload=[...])])
        items = provider.pull(kind="watchlist")
    """

    name = "local"

    def __init__(self, directory: str | Path = "sync_store") -> None:
        """Initialise the provider.

        Args:
            directory: Root directory for stored items.
        """
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        """Return the storage directory."""
        return self._dir

    # ------------------------------------------------------------------
    # SyncProvider interface
    # ------------------------------------------------------------------

    def push(self, items: list[SyncItem]) -> SyncResult:
        """Write items to JSON files.

        Args:
            items: Items to store.

        Returns:
            A :class:`SyncResult`.
        """
        synced = 0
        failed = 0
        details: dict[str, Any] = {}
        for item in items:
            try:
                kind_dir = self._dir / _kind_name(item.kind)
                kind_dir.mkdir(parents=True, exist_ok=True)
                path = kind_dir / f"{item.key}.json"
                path.write_text(
                    json.dumps(item.to_dict(), indent=2, default=str),
                    encoding="utf-8",
                )
                synced += 1
                details.setdefault(_kind_name(item.kind), 0)
                details[_kind_name(item.kind)] += 1
            except Exception as exc:
                failed += 1
                logger.warning("Sync push failed for %s/%s: %s", item.kind, item.key, exc)

        logger.info("Sync push: %d synced, %d failed.", synced, failed)
        return SyncResult(
            status=SyncStatus.SUCCESS if failed == 0 else SyncStatus.FAILED,
            message=f"Pushed {synced} item(s).",
            synced_items=synced,
            failed_items=failed,
            details=details,
        )

    def pull(self, kind: SyncKind | str | None = None) -> list[SyncItem]:
        """Load stored items from JSON files.

        Args:
            kind: Optional category filter.

        Returns:
            List of :class:`SyncItem`.
        """
        items: list[SyncItem] = []
        dirs = [self._dir / _kind_name(kind)] if kind is not None else list(self._dir.iterdir())

        for kind_dir in dirs:
            if not kind_dir.is_dir():
                continue
            for path in sorted(kind_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    items.append(
                        SyncItem(
                            kind=data.get("kind", kind_dir.name),
                            key=data.get("key", path.stem),
                            payload=data.get("payload"),
                            updated_at=data.get("updated_at", ""),
                        )
                    )
                except Exception as exc:
                    logger.warning("Sync pull failed for %s: %s", path, exc)
        return items

    def delete(self, kind: SyncKind | str, key: str) -> bool:
        """Remove a stored item.

        Args:
            kind: Data category.
            key: Item key.

        Returns:
            True when removed.
        """
        path = self._dir / _kind_name(kind) / f"{key}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def clear(self) -> None:
        """Remove every stored item."""
        for path in self._dir.rglob("*.json"):
            path.unlink()

    def describe(self) -> dict[str, Any]:
        """Return provider metadata."""
        return {"name": self.name, "kind": "local", "directory": str(self._dir)}


def _kind_name(kind: SyncKind | str) -> str:
    """Normalize a SyncKind to a directory name."""
    if isinstance(kind, SyncKind):
        return kind.value
    return str(kind).lower()
