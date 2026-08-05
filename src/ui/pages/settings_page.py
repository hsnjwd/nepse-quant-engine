"""Settings page — comprehensive application configuration."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.ui.helpers import (
    section_header,
    divider,
)
from src.ui.theme import theme


def render() -> None:
    """Render the comprehensive Settings page."""
    section_header("Settings", "Configure application preferences")

    tabs = st.tabs(["🎨 Appearance", "⚙️ Trading", "📡 Data", "🖥️ Display", "🔔 Notifications", "📊 Charts", "ℹ️ About"])

    # ── Tab 1: Appearance ────────────────────────────────────────
    with tabs[0]:
        st.markdown("##### Theme")
        current_theme = st.session_state.get("theme", "dark")
        theme_choice = st.selectbox(
            "Theme",
            ["dark", "light"],
            index=0 if current_theme == "dark" else 1,
            key="settings_theme",
        )
        if theme_choice != current_theme:
            st.session_state["theme"] = theme_choice
            st.rerun()

        st.markdown("##### Layout")
        st.checkbox("Show sidebar market status", value=True, key="settings_show_market_status")
        st.checkbox("Compact mode", value=False, key="settings_compact_mode")

    # ── Tab 2: Trading ──────────────────────────────────────────
    with tabs[1]:
        st.markdown("##### Trade Defaults")
        col1, col2 = st.columns(2)
        with col1:
            commission = st.number_input(
                "Commission (%)",
                min_value=0.0, max_value=5.0,
                value=st.session_state.get("commission", 0.1) * 100,
                step=0.05, format="%.2f",
                help="Broker commission per trade as a percentage",
                key="settings_commission",
            )
            st.session_state["commission"] = commission / 100.0
        with col2:
            risk_percent = st.number_input(
                "Risk per Trade (%)",
                min_value=0.1, max_value=50.0,
                value=st.session_state.get("risk_percent", 2.0),
                step=0.5, format="%.1f",
                help="Maximum risk per trade as a percentage of capital",
                key="settings_risk",
            )
            st.session_state["risk_percent"] = risk_percent

        col1, col2 = st.columns(2)
        with col1:
            default_capital = st.number_input(
                "Default Capital (₹)",
                min_value=1000.0, max_value=100_000_000.0,
                value=st.session_state.get("default_capital", 100000.0),
                step=10000.0, format="%.0f",
                help="Default capital for backtesting and paper trading",
                key="settings_capital",
            )
            st.session_state["default_capital"] = default_capital
        with col2:
            slippage = st.number_input(
                "Slippage (%)",
                min_value=0.0, max_value=5.0,
                value=st.session_state.get("slippage", 0.0) * 100 or 0.0,
                step=0.05, format="%.2f",
                help="Estimated slippage per trade",
                key="settings_slippage",
            )
            st.session_state["slippage"] = slippage / 100.0

    # ── Tab 3: Data ─────────────────────────────────────────────
    with tabs[2]:
        st.markdown("##### Cache")
        cache_ttl = st.number_input(
            "Cache TTL (seconds)",
            min_value=10, max_value=3600,
            value=st.session_state.get("cache_ttl", 30),
            step=10,
            help="How long market data is cached before refresh",
            key="settings_cache_ttl",
        )
        st.session_state["cache_ttl"] = cache_ttl

        st.markdown("##### API Provider Priority")
        providers = ["NEPSE API", "Fallback API", "CSV", "Cached"]
        current_priority = st.session_state.get("provider_priority", providers)
        priority = st.multiselect(
            "Provider order (top = tried first)",
            providers,
            default=current_priority if current_priority else providers,
            key="settings_provider_priority",
        )
        if priority:
            st.session_state["provider_priority"] = priority

        st.markdown("##### Data Directory")
        try:
            from src.config import DATA_DIRECTORY
            st.info(f"**Data Directory:** `{DATA_DIRECTORY}`")
        except Exception as e:
            st.warning(f"Data directory not configured: {e}")

        if st.button("🗑️ Clear Data Cache", use_container_width=True, type="secondary"):
            try:
                from src.data import DataService
                svc = DataService()
                svc.clear_cache()
                st.success("Cache cleared successfully.")
            except Exception as e:
                st.error(f"Failed to clear cache: {e}")

    # ── Tab 4: Display ──────────────────────────────────────────
    with tabs[3]:
        st.markdown("##### Auto-Refresh")
        refresh_rate = st.number_input(
            "Auto-Refresh Rate (seconds)",
            min_value=10, max_value=3600,
            value=st.session_state.get("refresh_rate", 60),
            step=10,
            help="How often data refreshes automatically",
            key="settings_refresh_rate",
        )
        st.session_state["refresh_rate"] = refresh_rate

        st.markdown("##### Number Format")
        st.selectbox(
            "Currency symbol",
            ["₹ (NPR)", "$ (USD)", "₨ (PKR)"],
            index=0,
            key="settings_currency",
        )
        st.selectbox(
            "Decimal separator",
            ["1,234.56", "1 234,56", "1.234,56"],
            index=0,
            key="settings_decimal",
        )

    # ── Tab 5: Notifications ─────────────────────────────────────
    with tabs[4]:
        st.markdown("##### Alert Notifications")
        st.checkbox("Enable price alerts", value=True, key="settings_price_alerts")
        st.checkbox("Enable volume alerts", value=True, key="settings_volume_alerts")
        st.checkbox("Enable RSI alerts", value=True, key="settings_rsi_alerts")
        st.checkbox("Enable MACD alerts", value=True, key="settings_macd_alerts")
        st.checkbox("Enable breakout alerts", value=False, key="settings_breakout_alerts")
        st.checkbox("Enable regime change alerts", value=True, key="settings_regime_alerts")
        st.checkbox("Enable portfolio alerts", value=True, key="settings_portfolio_alerts")

        st.markdown("##### Notification Priority")
        st.select_slider(
            "Minimum priority level",
            options=["Info", "Low", "Medium", "High", "Critical"],
            value="Medium",
            key="settings_min_priority",
        )

    # ── Tab 6: Charts ────────────────────────────────────────────
    with tabs[5]:
        st.markdown("##### Chart Defaults")

        col1, col2 = st.columns(2)
        with col1:
            st.selectbox(
                "Default chart type",
                ["Candlestick", "Line", "OHLC", "Area"],
                index=0,
                key="settings_chart_type",
            )
            st.selectbox(
                "Color scheme",
                ["Dark", "Light", "NEPSE"],
                index=0,
                key="settings_chart_scheme",
            )
        with col2:
            st.selectbox(
                "Default time period",
                ["1M", "3M", "6M", "1Y", "2Y", "5Y"],
                index=3,
                key="settings_chart_period",
            )
            st.selectbox(
                "Chart theme",
                ["Plotly Dark", "Plotly Light", "Solarized"],
                index=0,
                key="settings_chart_theme",
            )

        st.checkbox("Show volume by default", value=True, key="settings_show_volume")
        st.checkbox("Show moving averages by default", value=True, key="settings_show_ma")
        st.checkbox("Show Bollinger Bands by default", value=False, key="settings_show_bb")
        st.checkbox("Show RSI by default", value=True, key="settings_show_rsi")

    # ── Tab 7: About ────────────────────────────────────────────
    with tabs[6]:
        st.markdown("##### ℹ️ NEPSE Quant Engine")
        st.markdown("""
        **Version:** v0.6.0

        **Description:** Quantitative trading engine for NEPSE stock market analysis,
        backtesting, and portfolio management.

        **Tech Stack:**
        - Python 3.12+
        - Streamlit (Frontend)
        - FastAPI (Backend API)
        - SQLite (Portfolio Storage)
        - Plotly (Charts)
        - DataService (Data Layer)

        **Features:**
        - 📊 Live market dashboard
        - 🔍 Real-time stock scanner with signals
        - 📈 Advanced technical charts with 10+ indicators
        - 💼 Portfolio management with SQLite persistence
        - 📝 Paper trading engine with market/limit/stop-loss orders
        - ⏪ Strategy backtesting with parameter optimization
        - 🔔 Smart alert center with 7 alert types
        - 🌦️ Market regime detection
        - 📤 Multi-format export (CSV, Excel, JSON, HTML)

        **Links:**
        - [Documentation](docs/)
        - [GitHub](https://github.com/nepse-quant-engine)
        """)

    # ── Reset ───────────────────────────────────────────────────
    divider()
    if st.button("🔄 Reset All Settings", use_container_width=True, type="secondary"):
        keys_to_reset = [
            "theme", "refresh_rate", "commission", "risk_percent",
            "default_capital", "slippage", "cache_ttl",
            "auto_refresh", "provider_priority",
        ]
        for key in keys_to_reset:
            if key in st.session_state:
                del st.session_state[key]
        st.success("Settings reset to defaults.")
        st.rerun()
