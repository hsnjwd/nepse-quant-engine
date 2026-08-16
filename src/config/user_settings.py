"""Persistent user settings management.

Stores all user-configurable settings in a JSON file at ``~/.nepse/user_settings.json``.
Settings are loaded automatically on import and saved on every change.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# User-state root. Defaults to ``~/.nepse``; overridable via NEPSE_HOME
# so tests can isolate from real user data.
NEPSE_HOME = Path(os.environ.get("NEPSE_HOME", str(Path.home() / ".nepse")))

SETTINGS_DIR = NEPSE_HOME
SETTINGS_FILE = SETTINGS_DIR / "user_settings.json"

# Default settings
DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "dark",
    "theme_variant": "default",
    "refresh_rate": 60,
    "auto_refresh": "Manual",
    "commission": 0.001,
    "risk_percent": 2.0,
    "default_capital": 100000.0,
    "slippage": 0.0,
    "cache_ttl": 30,
    "provider_priority": ["NEPSE API", "Fallback API", "CSV", "Cached"],
    "show_market_status": True,
    "compact_mode": False,
    "chart_type": "Candlestick",
    "chart_period": "1Y",
    "chart_theme": "Plotly Dark",
    "show_volume": True,
    "show_ma": True,
    "show_bb": False,
    "show_rsi": True,
    "currency": "₹ (NPR)",
    "decimal_format": "1,234.56",
    "enable_price_alerts": True,
    "enable_volume_alerts": True,
    "enable_rsi_alerts": True,
    "enable_macd_alerts": True,
    "enable_breakout_alerts": False,
    "enable_regime_alerts": True,
    "enable_portfolio_alerts": True,
    "min_priority": "Medium",
    "websocket_enabled": False,
    "enable_performance_monitoring": True,
}


class UserSettings:
    """Thread-safe persistent user settings backed by JSON file.

    Usage::
        settings = UserSettings()
        theme = settings.get("theme")
        settings.set("theme", "light")
        settings.save()
    """

    _instance: UserSettings | None = None
    _lock = threading.Lock()

    def __new__(cls) -> UserSettings:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialised"):
            return
        self._data: dict[str, Any] = {}
        self._dirty = False
        self._load()
        self._initialised = True

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value by key."""
        with self._lock:
            if key in self._data:
                return self._data[key]
            return DEFAULT_SETTINGS.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value and persist to disk."""
        with self._lock:
            self._data[key] = value
        self.save()

    def set_many(self, updates: dict[str, Any]) -> None:
        """Set multiple settings at once."""
        with self._lock:
            self._data.update(updates)
        self.save()

    def get_all(self) -> dict[str, Any]:
        """Get all settings as a dict."""
        with self._lock:
            result = dict(DEFAULT_SETTINGS)
            result.update(self._data)
            return result

    def reset(self) -> None:
        """Reset all settings to defaults."""
        with self._lock:
            self._data = {}
        self.save()

    def reset_key(self, key: str) -> None:
        """Reset a single key to its default value."""
        if key in DEFAULT_SETTINGS:
            with self._lock:
                self._data.pop(key, None)
            self.save()

    def save(self) -> None:
        """Persist settings to disk."""
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(SETTINGS_FILE, "w") as f:
                    json.dump(self._data, f, indent=2)
        except Exception as exc:
            logger.warning("[UserSettings] Save error: %s", exc)

    def _load(self) -> None:
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE) as f:
                    self._data = json.load(f)
                logger.debug("[UserSettings] Loaded %d settings from %s", len(self._data), SETTINGS_FILE)
            else:
                self._data = {}
                logger.debug("[UserSettings] No settings file found, using defaults")
        except Exception as exc:
            logger.warning("[UserSettings] Load error: %s", exc)
            self._data = {}

    def apply_to_session_state(self) -> None:
        """Apply all saved settings to Streamlit session state."""
        import streamlit as st
        for key, value in self.get_all().items():
            if not key.startswith("_"):
                st.session_state[key] = value

    def sync_from_session_state(self) -> None:
        """Sync settings from Streamlit session state back to disk."""
        import streamlit as st
        for key in DEFAULT_SETTINGS:
            if key in st.session_state:
                self._data[key] = st.session_state[key]
        self.save()
