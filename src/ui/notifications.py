"""Unified notification center for the NEPSE Quant Engine.

Provides a singleton NotificationManager that collects, persists, and
displays notifications from all sources (price alerts, scanner, portfolio,
paper trading, market regime, WebSocket, system, cache).

Features:
- Notification drawer with categorized view
- Unread badge count
- Read/unread/dismiss/dismiss-all
- Priority colors (Success, Warning, Critical, Info)
- 8 categories: Portfolio, Scanner, Price, Market, WebSocket, System, Background
- Persistent JSON-backed history
- Search and filter
- Configurable max history size
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import streamlit as st

logger = logging.getLogger(__name__)

# User-state root. Defaults to ``~/.nepse``; overridable via NEPSE_HOME
# so tests can isolate from real user data.
NEPSE_HOME = Path(os.environ.get("NEPSE_HOME", str(Path.home() / ".nepse")))

NOTIF_DIR = NEPSE_HOME
NOTIF_FILE = NOTIF_DIR / "notifications.json"

MAX_HISTORY = 500


class NotificationCategory(str, Enum):
    PORTFOLIO = "portfolio"
    SCANNER = "scanner"
    PRICE = "price"
    MARKET = "market"
    WEBSOCKET = "websocket"
    SYSTEM = "system"
    BACKGROUND = "background"
    TRADE = "trade"
    AI = "ai"


class NotificationPriority(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Notification:
    """A single notification entry."""

    id: str = ""
    title: str = ""
    message: str = ""
    category: str = NotificationCategory.SYSTEM.value
    priority: str = NotificationPriority.INFO.value
    symbol: str = ""
    read: bool = False
    dismissed: bool = False
    timestamp: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_unread(self) -> bool:
        return not self.read and not self.dismissed


class NotificationManager:
    """Central notifications manager. Singleton, thread-safe, JSON-persisted.

    Usage::
        mgr = NotificationManager()
        mgr.notify("Trade executed", "Bought 100 NABIL @ 500", category="trade", priority="success")
        badge = mgr.unread_count
        mgr.mark_read(notif.id)
        mgr.dismiss_all()
    """

    _instance: NotificationManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> NotificationManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialised"):
            return
        self._notifications: list[Notification] = []
        self._counter = 0
        self._load()
        self._initialised = True

    # ── Public API ───────────────────────────────────────────────

    def notify(
        self,
        title: str,
        message: str = "",
        category: str = NotificationCategory.SYSTEM.value,
        priority: str = NotificationPriority.INFO.value,
        symbol: str = "",
        data: dict[str, Any] | None = None,
    ) -> Notification:
        """Create a new notification."""
        self._counter += 1
        notif = Notification(
            id=f"N{self._counter:06d}",
            title=title,
            message=message,
            category=category,
            priority=priority,
            symbol=symbol.upper() if symbol else "",
            timestamp=datetime.now().isoformat(),
            data=data or {},
        )
        with self._lock:
            self._notifications.insert(0, notif)
            if len(self._notifications) > MAX_HISTORY:
                self._notifications = self._notifications[:MAX_HISTORY]
        self._save()
        logger.debug("[Notifications] %s: %s", notif.priority.upper(), title)
        return notif

    def notify_portfolio(self, title: str, message: str = "", priority: str = NotificationPriority.INFO.value) -> Notification:
        """Create a portfolio notification; returns the Notification."""
        return self.notify(title, message, "portfolio", priority)

    def notify_scanner(self, title: str, message: str = "", priority: str = NotificationPriority.INFO.value) -> Notification:
        """Create a scanner notification; returns the Notification."""
        return self.notify(title, message, "scanner", priority)

    def notify_price(self, symbol: str, title: str, message: str = "") -> Notification:
        """Create a price alert notification; returns the Notification."""
        return self.notify(title, message, "price", NotificationPriority.WARNING.value, symbol=symbol)

    def notify_market(self, title: str, message: str = "") -> Notification:
        """Create a market notification; returns the Notification."""
        return self.notify(title, message, "market", NotificationPriority.INFO.value)

    def notify_ws(self, title: str, message: str = "", priority: str = NotificationPriority.INFO.value) -> Notification:
        """Create a WebSocket notification; returns the Notification."""
        return self.notify(title, message, "websocket", priority)

    def notify_system(self, title: str, message: str = "", priority: str = NotificationPriority.INFO.value) -> Notification:
        """Create a system notification; returns the Notification."""
        return self.notify(title, message, "system", priority)

    def notify_trade(self, title: str, message: str = "", priority: str = NotificationPriority.SUCCESS.value) -> Notification:
        """Create a trade notification; returns the Notification."""
        return self.notify(title, message, "trade", priority)

    def notify_ai(self, title: str, message: str = "", priority: str = NotificationPriority.INFO.value) -> Notification:
        """Notify about an AI recommendation (Part 7 — AI notifications).

        Returns:
            The created :class:`Notification`.
        """
        return self.notify(title, message, "ai", priority)

    def notify_model(self, title: str, message: str = "", priority: str = NotificationPriority.SUCCESS.value) -> Notification:
        """Notify about ML model training / promotion events.

        Returns:
            The created :class:`Notification`.
        """
        return self.notify(title, message, "ai", priority)

    def notify_strategy(self, title: str, message: str = "", priority: str = NotificationPriority.INFO.value) -> Notification:
        """Notify about strategy optimization completion.

        Returns:
            The created :class:`Notification`.
        """
        return self.notify(title, message, "scanner", priority)

    def notify_risk(self, title: str, message: str = "", priority: str = NotificationPriority.WARNING.value) -> Notification:
        """Notify about risk alerts.

        Returns:
            The created :class:`Notification`.
        """
        return self.notify(title, message, "portfolio", priority)

    def notify_rebalance(self, title: str, message: str = "", priority: str = NotificationPriority.WARNING.value) -> Notification:
        """Notify about portfolio rebalancing suggestions.

        Returns:
            The created :class:`Notification`.
        """
        return self.notify(title, message, "portfolio", priority)

    # ── Queries ──────────────────────────────────────────────────

    @property
    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._notifications if n.is_unread)

    def get_all(self, limit: int = 100) -> list[Notification]:
        with self._lock:
            return [n for n in self._notifications if not n.dismissed][:limit]

    def get_by_category(self, category: str, limit: int = 100) -> list[Notification]:
        with self._lock:
            return [n for n in self._notifications if n.category == category and not n.dismissed][:limit]

    def get_unread(self, limit: int = 100) -> list[Notification]:
        with self._lock:
            return [n for n in self._notifications if n.is_unread][:limit]

    def search(self, query: str, limit: int = 100) -> list[Notification]:
        q = query.lower()
        with self._lock:
            return [
                n for n in self._notifications
                if not n.dismissed and (q in n.title.lower() or q in n.message.lower() or q in n.symbol.lower())
            ][:limit]

    # ── Actions ──────────────────────────────────────────────────

    def mark_read(self, notif_id: str) -> bool:
        with self._lock:
            for n in self._notifications:
                if n.id == notif_id:
                    n.read = True
                    self._save()
                    return True
        return False

    def mark_all_read(self) -> None:
        with self._lock:
            for n in self._notifications:
                n.read = True
        self._save()

    def dismiss(self, notif_id: str) -> bool:
        with self._lock:
            for n in self._notifications:
                if n.id == notif_id:
                    n.dismissed = True
                    self._save()
                    return True
        return False

    def dismiss_all(self) -> None:
        with self._lock:
            for n in self._notifications:
                n.dismissed = True
        self._save()

    def dismiss_by_category(self, category: str) -> None:
        with self._lock:
            for n in self._notifications:
                if n.category == category:
                    n.dismissed = True
        self._save()

    def clear_all(self) -> None:
        with self._lock:
            self._notifications.clear()
        self._save()

    def delete(self, notif_id: str) -> bool:
        with self._lock:
            for n in self._notifications:
                if n.id == notif_id:
                    self._notifications.remove(n)
                    self._save()
                    return True
        return False

    # ── Persistence ──────────────────────────────────────────────

    def _save(self) -> None:
        try:
            NOTIF_DIR.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "id": n.id, "title": n.title, "message": n.message,
                    "category": n.category, "priority": n.priority,
                    "symbol": n.symbol, "read": n.read, "dismissed": n.dismissed,
                    "timestamp": n.timestamp, "data": n.data,
                }
                for n in self._notifications
            ]
            with open(NOTIF_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("[Notifications] Save error: %s", exc)

    def _load(self) -> None:
        try:
            if NOTIF_FILE.exists():
                with open(NOTIF_FILE) as f:
                    data = json.load(f)
                for item in data:
                    self._notifications.append(Notification(
                        id=item.get("id", ""),
                        title=item.get("title", ""),
                        message=item.get("message", ""),
                        category=item.get("category", "system"),
                        priority=item.get("priority", "info"),
                        symbol=item.get("symbol", ""),
                        read=item.get("read", False),
                        dismissed=item.get("dismissed", False),
                        timestamp=item.get("timestamp", ""),
                        data=item.get("data", {}),
                    ))
                self._counter = max(
                    (int(n.id[1:]) for n in self._notifications if n.id.startswith("N")),
                    default=0,
                )
        except Exception as exc:
            logger.warning("[Notifications] Load error: %s", exc)


# ── Streamlit UI helpers ──────────────────────────────────────────


def render_notification_badge() -> str:
    """Return an HTML badge string for the sidebar notification icon."""
    mgr = NotificationManager()
    count = mgr.unread_count
    if count > 0:
        return f"""<span style="
            background: #FF5252; color: white; border-radius: 10px;
            padding: 1px 7px; font-size: 0.7rem; font-weight: 700;
            margin-left: 4px;">{min(count, 99)}{'+' if count > 99 else ''}</span>"""
    return ""


def render_notification_drawer(max_display: int = 50) -> None:
    """Render a Streamlit notification drawer/table."""
    mgr = NotificationManager()
    notifications = mgr.get_all(limit=max_display)

    if not notifications:
        st.info("No notifications.")
        return

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("✓ Mark All Read", use_container_width=True):
            mgr.mark_all_read()
            st.rerun()
    with col2:
        if st.button("🗑️ Dismiss All", use_container_width=True):
            mgr.dismiss_all()
            st.rerun()
    with col3:
        if st.button("❌ Clear All", use_container_width=True):
            mgr.clear_all()
            st.rerun()

    # Priority colors
    color_map = {
        "success": "#00C853",
        "warning": "#FFC107",
        "critical": "#FF5252",
        "info": "#2196F3",
    }
    icon_map = {
        "portfolio": "💼", "scanner": "🔍", "price": "💰",
        "market": "📊", "websocket": "📡", "system": "⚙️",
        "background": "🔄", "trade": "📝", "ai": "🤖",
    }

    rows = []
    for n in notifications:
        color = color_map.get(n.priority, "#9DA3B0")
        icon = icon_map.get(n.category, "📌")
        badge = f"<span style='color:{color}; font-weight:600;'>●</span>"
        rows.append({
            "": badge,
            "Time": n.timestamp[11:19] if len(n.timestamp) >= 19 else n.timestamp,
            "Cat": f"{icon} {n.category.title()}",
            "Title": n.title,
            "Message": n.message[:80] + "..." if len(n.message) > 80 else n.message,
            "Symbol": n.symbol if n.symbol else "—",
            "Read": "✓" if n.read else "○",
        })

    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


notification_manager = NotificationManager()
