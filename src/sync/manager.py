"""Sync manager — orchestrates syncing of application data categories.

Collects application state (watchlist, settings, portfolio, alerts,
strategies) into :class:`SyncItem` batches and delegates push/pull to
a :class:`SyncProvider`.
"""

from __future__ import annotations

import logging
from typing import Any

from src.sync.base import (
    SyncItem,
    SyncKind,
    SyncManager as _SyncManager,
    SyncProvider,
    SyncResult,
)

logger = logging.getLogger("nepse.sync.manager")


class SyncManager(_SyncManager):
    """High-level sync orchestrator with app-aware collectors."""

    def collect_watchlist(self) -> list[SyncItem]:
        """Collect the current watchlist as sync items."""
        try:
            from src.data import DataService

            symbols = DataService().get_watchlist()
            return [
                SyncItem(kind=SyncKind.WATCHLIST, key="default", payload=symbols)
            ]
        except Exception as exc:
            logger.warning("Failed to collect watchlist: %s", exc)
            return []

    def collect_settings(self) -> list[SyncItem]:
        """Collect user settings as sync items."""
        try:
            from src.config.user_settings import load_settings

            return [
                SyncItem(
                    kind=SyncKind.SETTINGS,
                    key="user",
                    payload=load_settings(),
                )
            ]
        except Exception:
            return [SyncItem(kind=SyncKind.SETTINGS, key="user", payload={})]

    def collect_portfolio(self) -> list[SyncItem]:
        """Collect the persistent portfolio as sync items."""
        try:
            from src.portfolio.storage import PortfolioStorage

            storage = PortfolioStorage()
            return [
                SyncItem(
                    kind=SyncKind.PORTFOLIO,
                    key="holdings",
                    payload=storage.export_dict(),
                )
            ]
        except Exception as exc:
            logger.warning("Failed to collect portfolio: %s", exc)
            return []

    def collect_all(self) -> list[SyncItem]:
        """Collect every supported data category.

        Returns:
            Combined list of :class:`SyncItem`.
        """
        items: list[SyncItem] = []
        items.extend(self.collect_watchlist())
        items.extend(self.collect_settings())
        items.extend(self.collect_portfolio())
        return items

    def sync_now(self) -> SyncResult:
        """Collect and push all application data.

        Returns:
            A :class:`SyncResult`.
        """
        items = self.collect_all()
        if not items:
            return SyncResult(
                status="skipped",
                message="Nothing to sync.",
            )
        return self.provider.push(items)

    def restore(self, items: list[SyncItem]) -> dict[str, Any]:
        """Restore application state from pulled items.

        Args:
            items: Items pulled from the provider.

        Returns:
            Dict describing what was restored.
        """
        restored: dict[str, Any] = {"watchlist": 0, "settings": 0, "portfolio": 0}
        for item in items:
            kind = item.kind.value if isinstance(item.kind, SyncKind) else str(item.kind)
            if kind == "watchlist" and item.payload:
                try:
                    from src.data import DataService

                    svc = DataService()
                    for symbol in item.payload:
                        if symbol not in svc.get_watchlist():
                            svc.add_to_watchlist(symbol)
                    restored["watchlist"] += 1
                except Exception as exc:
                    logger.warning("Watchlist restore failed: %s", exc)
            elif kind == "settings" and item.payload:
                try:
                    from src.config.user_settings import save_settings

                    save_settings(item.payload)
                    restored["settings"] += 1
                except Exception as exc:
                    logger.warning("Settings restore failed: %s", exc)
        logger.info("Sync restore complete: %s", restored)
        return restored
