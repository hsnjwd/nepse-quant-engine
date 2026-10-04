"""Session state management for the NEPSE Quant Engine UI.

Provides typed accessors and initialisers for all session-level
data used across pages.
"""

from __future__ import annotations

import time
from typing import Any

import streamlit as st


# ── Initialisation helpers ────────────────────────────────────────


def _init_key(key: str, default: Any) -> None:
    """Set *key* to *default* in session state if not already present."""
    if key not in st.session_state:
        st.session_state[key] = default


def initialise_state() -> None:
    """Initialise all session-state keys used by the application."""
    # Navigation
    _init_key("page", "dashboard")

    # Settings
    _init_key("theme", "dark")
    _init_key("refresh_rate", 60)
    _init_key("commission", 0.001)
    _init_key("risk_percent", 2.0)
    _init_key("default_capital", 100000.0)
    _init_key("auto_refresh", "Manual")

    # Scanner
    _init_key("scanner_results", [])
    _init_key("scanner_loading", False)
    _init_key("scanner_cache", None)
    _init_key("scanner_last_refresh", None)

    # Market summary cache
    _init_key("market_summary", None)
    _init_key("market_summary_last_refresh", None)

    # Watchlist
    _init_key("watchlist", [])
    _init_key("watchlist_data", {})
    _init_key("watchlist_last_refresh", None)

    # Analysis
    _init_key("current_symbol", "")
    _init_key("analysis_result", None)
    _init_key("analysis_df", None)

    # Portfolio
    _init_key("portfolio", {"holdings": [], "cash": 0.0})
    _init_key("trade_history", [])

    # Paper trading
    _init_key("paper_positions", [])
    _init_key("paper_balance", 1000000.0)
    _init_key("paper_trades", [])

    # Regime
    _init_key("regime_result", None)
    _init_key("regime_df", None)
    _init_key("regime_symbol", "")

    # Backtest
    _init_key("backtest_result", None)
    _init_key("backtest_loading", False)
    _init_key("backtest_symbol", "")

    # Optimizer
    _init_key("optimizer_result", None)
    _init_key("optimizer_loading", False)
    _init_key("optimizer_symbol", "")

    # Alerts
    _init_key("alerts", [])
    _init_key("alerts_loading", False)
    _init_key("alerts_last_refresh", None)

    # Reports
    _init_key("report_data", None)
    _init_key("market_report_data", None)

    # Live data (NEPSE API)
    _init_key("live_stocks", [])

    # Top gainers / losers cache
    _init_key("top_gainers", [])
    _init_key("top_losers", [])
    _init_key("top_turnover", [])


# ── Typed accessors ───────────────────────────────────────────────


def get_page() -> str:
    """Return the current active page name."""
    return st.session_state.get("page", "dashboard")


def set_page(page: str) -> None:
    """Navigate to *page*."""
    st.session_state["page"] = page


def get_theme() -> str:
    """Return the current theme ('dark' or 'light')."""
    return st.session_state.get("theme", "dark")


def set_theme(t: str) -> None:
    """Set the theme."""
    st.session_state["theme"] = t


def get_refresh_rate() -> int:
    """Return the auto-refresh interval in seconds.

    Maps the ``auto_refresh`` setting to a number:
        - ``"Manual"`` → 0
        - ``"10 sec"`` → 10
        - ``"30 sec"`` → 30
        - ``"1 min"`` → 60
        - ``"5 min"`` → 300
    """
    mode = st.session_state.get("auto_refresh", "Manual")
    mapping = {
        "Manual": 0,
        "10 sec": 10,
        "30 sec": 30,
        "1 min": 60,
        "5 min": 300,
    }
    return mapping.get(mode, 0)


def get_auto_refresh_mode() -> str:
    """Return the current auto-refresh mode label."""
    return st.session_state.get("auto_refresh", "Manual")


def get_commission() -> float:
    """Return the commission rate as a decimal."""
    return st.session_state.get("commission", 0.001)


def get_risk_percent() -> float:
    """Return the risk-per-trade as a percentage."""
    return st.session_state.get("risk_percent", 2.0)


def get_default_capital() -> float:
    """Return the default backtesting capital."""
    return st.session_state.get("default_capital", 100000.0)


def should_refresh(last_ts: Any, ttl_seconds: int = 30) -> bool:
    """Check whether cached data has expired.

    Args:
        last_ts: A timestamp stored in session state (``time.time()``).
        ttl_seconds: Maximum age in seconds before a refresh is needed.

    Returns:
        ``True`` if the cache is stale or missing.
    """
    if last_ts is None:
        return True
    return (time.time() - last_ts) > ttl_seconds


def touch_cache(key: str) -> None:
    """Update the ``*_last_refresh`` key for *key* to the current time.

    Example: ``touch_cache("scanner")`` sets ``scanner_last_refresh``.
    """
    st.session_state[f"{key}_last_refresh"] = time.time()
