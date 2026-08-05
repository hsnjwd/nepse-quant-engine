"""NEPSE Quant Engine — Streamlit Frontend.

Usage:
    python -m streamlit run app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import logging
import streamlit as st

# ── Ensure project root is on sys.path ──────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Page config (MUST be first Streamlit command) ───────────────
st.set_page_config(
    page_title="NEPSE Quant Engine",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Load custom CSS ─────────────────────────────────────────────
css_path = ROOT / "assets" / "style.css"
if css_path.exists():
    with open(css_path, encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ── Initialise session state ────────────────────────────────────
from src.ui.state import initialise_state, get_refresh_rate
initialise_state()

# ── Cache DataService singleton ────────────────────────────────
@st.cache_resource
def _get_dataservice() -> Any:
    """Return the DataService singleton, cached by Streamlit.

    ``@st.cache_resource`` ensures the service is only instantiated
    once per session, avoiding repeated provider/WebSocket setup.
    """
    from src.data import DataService
    return DataService()

# Eagerly initialise DataService so background refresh starts ASAP
_ds = _get_dataservice()
_ds.start_background_refresh()

# ── Import theme ────────────────────────────────────────────────
from src.ui.theme import theme
from src.ui.notifications import notification_manager as nm
from src.ui.shortcuts import inject_keyboard_shortcuts, render_shortcuts_help

# ── Inject keyboard shortcuts ───────────────────────────────────
inject_keyboard_shortcuts()


# ═══════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(
        f"""
        <div style="text-align: center; padding: 1rem 0;">
            <h1 style="color: {theme.primary}; font-size: 1.5rem; margin: 0;">📈 NEPSE</h1>
            <p style="color: {theme.text_secondary}; font-size: 0.8rem; margin: 0;">
                Quant Engine
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── Navigation ──────────────────────────────────────────────
    def nav_button(label: str, page: str, icon: str = "", indent: bool = False) -> bool:
        """Render a sidebar navigation button."""
        prefix = "&nbsp;&nbsp;&nbsp;&nbsp;" if indent else ""
        text = f"{prefix}{icon} {label}" if icon else f"{prefix}{label}"
        active = st.session_state.get("page", "dashboard") == page
        btn_type = "primary" if active else "secondary"
        clicked = st.button(text, key=f"nav_{page}", use_container_width=True, type=btn_type)
        if clicked:
            st.session_state["page"] = page
            st.rerun()
        return clicked

    st.markdown(
        f"<p style='color: {theme.text_muted}; font-size: 0.7rem; "
        f"text-transform: uppercase; letter-spacing: 0.1em; margin: 0.5rem 0 0.25rem;'>"
        f"Main</p>",
        unsafe_allow_html=True,
    )
    nav_button("Dashboard", "dashboard", "🏠")

    st.markdown(
        f"<p style='color: {theme.text_muted}; font-size: 0.7rem; "
        f"text-transform: uppercase; letter-spacing: 0.1em; margin: 0.5rem 0 0.25rem;'>"
        f"Market</p>",
        unsafe_allow_html=True,
    )
    nav_button("Scanner", "scanner", "🔍", indent=True)
    nav_button("Analyze Stock", "analyze", "📊", indent=True)
    nav_button("Advanced Charts", "advanced_charts", "📈", indent=True)
    nav_button("Market Regime", "regime", "🌦️", indent=True)
    nav_button("Market Replay", "replay", "📼", indent=True)
    nav_button("Upload OHLCV", "upload", "📤", indent=True)

    st.markdown(
        f"<p style='color: {theme.text_muted}; font-size: 0.7rem; "
        f"text-transform: uppercase; letter-spacing: 0.1em; margin: 0.5rem 0 0.25rem;'>"
        f"Portfolio</p>",
        unsafe_allow_html=True,
    )
    nav_button("Portfolio", "portfolio", "💼", indent=True)
    nav_button("Portfolio Analytics", "portfolio_analytics", "📊", indent=True)
    nav_button("Paper Trading", "paper_trading", "📝", indent=True)

    st.markdown(
        f"<p style='color: {theme.text_muted}; font-size: 0.7rem; "
        f"text-transform: uppercase; letter-spacing: 0.1em; margin: 0.5rem 0 0.25rem;'>"
        f"Strategies</p>",
        unsafe_allow_html=True,
    )
    nav_button("Backtest", "backtest", "⏪", indent=True)
    nav_button("Backtest Studio", "backtest_studio", "🎛️", indent=True)
    nav_button("Strategy Comparison", "strategy_comparison", "🏟️", indent=True)
    nav_button("Execution Analysis", "execution_analysis", "⚙️", indent=True)
    nav_button("Optimization Lab", "optimization_lab", "🔬", indent=True)
    nav_button("Scenario Lab", "scenario_lab", "🧪", indent=True)
    nav_button("Trade Explorer", "trade_explorer", "🔍", indent=True)
    nav_button("Performance Report", "performance_report", "📊", indent=True)
    nav_button("Optimizer", "optimizer", "⚡", indent=True)
    nav_button("Portfolio Optimizer", "portfolio_optimizer", "⚖️", indent=True)
    nav_button("Strategy Builder", "strategy_builder", "🧩", indent=True)
    nav_button("Strategy Marketplace", "strategy_marketplace", "🛒", indent=True)
    nav_button("Genetic Optimizer", "genetic_optimizer", "🧬", indent=True)

    st.markdown(
        f"<p style='color: {theme.text_muted}; font-size: 0.7rem; "
        f"text-transform: uppercase; letter-spacing: 0.1em; margin: 0.5rem 0 0.25rem;'>"
        f"AI & Risk</p>",
        unsafe_allow_html=True,
    )
    nav_button("AI Advisor", "ai_advisor", "🤖")
    nav_button("ML Models", "ml_models", "🧠", indent=True)
    nav_button("Monte Carlo Lab", "monte_carlo_lab", "🎲", indent=True)
    nav_button("Risk Dashboard", "risk_dashboard", "🛡️", indent=True)

    st.markdown(
        f"<p style='color: {theme.text_muted}; font-size: 0.7rem; "
        f"text-transform: uppercase; letter-spacing: 0.1em; margin: 0.5rem 0 0.25rem;'>"
        f"System</p>",
        unsafe_allow_html=True,
    )
    nav_button("Broker Manager", "broker_manager", "🏦")
    nav_button("Plugin Manager", "plugin_manager", "🔌", indent=True)
    nav_button("Cloud Sync", "cloud_sync", "☁️", indent=True)
    nav_button("API Explorer", "api_explorer", "🧪", indent=True)
    nav_button("Settings", "settings", "⚙️")
    nav_button("Cache & Performance", "cache", "📦", indent=True)
    nav_button("System Status", "system_status", "🩺", indent=True)

    st.markdown("---")

    # ── Market Status ───────────────────────────────────────────
    try:
        from src.data import DataService
        svc = DataService()
        summary = svc.get_market_summary()
        ws_connected = svc.is_live_feed_connected()
        status_color = theme.success if summary.status == "Open" else theme.danger
        status_emoji = "🟢" if summary.status == "Open" else "🔴"
        ws_emoji = "🟢" if ws_connected else "⚪"
        st.markdown(
            f"""
            <div style="
                background: {theme.card_bg};
                border-radius: 8px;
                padding: 0.75rem;
                margin-bottom: 0.5rem;
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: {theme.text_secondary}; font-size: 0.8rem;">Market</span>
                    <span style="color: {status_color}; font-size: 0.85rem; font-weight: 600;">
                        {status_emoji} {summary.status}
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-top: 4px;">
                    <span style="color: {theme.text_muted}; font-size: 0.75rem;">NEPSE</span>
                    <span style="color: {theme.text}; font-size: 0.85rem; font-weight: 600;">
                        {summary.index:,.2f}
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-top: 2px;">
                    <span style="color: {theme.text_muted}; font-size: 0.75rem;">Change</span>
                    <span style="color: {'#FF5252' if summary.change < 0 else '#00C853'}; font-size: 0.85rem;">
                        {summary.change:+.2f} ({summary.change_pct:+.2f}%)
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-top: 4px;">
                    <span style="color: {theme.text_muted}; font-size: 0.75rem;">Live Feed</span>
                    <span style="color: {'#00C853' if ws_connected else theme.text_muted}; font-size: 0.8rem;">
                        {ws_emoji} {'Connected' if ws_connected else 'Polling'}
                    </span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # ── Notification badge ──────────────────────────────────────
    notif_count = nm.unread_count
    if notif_count > 0:
        st.markdown(
            f"""
            <div style="
                background: {theme.card_bg};
                border-radius: 8px;
                padding: 0.5rem 0.75rem;
                margin-bottom: 0.5rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            ">
                <span style="color: {theme.text};">🔔 Notifications</span>
                <span style="
                    background: #FF5252;
                    color: white;
                    border-radius: 10px;
                    padding: 1px 8px;
                    font-size: 0.75rem;
                    font-weight: 700;
                ">{min(notif_count, 99)}{'+' if notif_count > 99 else ''}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Auto-refresh selector ───────────────────────────────────
    refresh_modes = ["Manual", "10 sec", "30 sec", "1 min", "5 min"]
    current_mode = st.session_state.get("auto_refresh", "Manual")
    idx = refresh_modes.index(current_mode) if current_mode in refresh_modes else 0
    auto_refresh = st.selectbox(
        "Auto-Refresh",
        refresh_modes,
        index=idx,
        key="auto_refresh_select",
        label_visibility="collapsed",
    )
    if auto_refresh != st.session_state.get("auto_refresh"):
        st.session_state["auto_refresh"] = auto_refresh
        st.rerun()

    # ── Keyboard shortcuts help overlay ─────────────────────────
    render_shortcuts_help()

    # ── Footer ──────────────────────────────────────────────────
    st.markdown(
        f"""
        <div style="text-align: center;">
            <p style="color: {theme.text_muted}; font-size: 0.7rem;">
                NEPSE Quant Engine v1.0.0-rc1 — Press <kbd style="background: {theme.input_bg}; padding: 1px 6px; border-radius: 3px; font-size: 0.7rem;">?</kbd> for shortcuts
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ═══════════════════════════════════════════════════════════════════
# Auto-refresh logic
# ═══════════════════════════════════════════════════════════════════

refresh_rate = get_refresh_rate()
if refresh_rate > 0:
    last_refresh = st.session_state.get("_app_last_refresh", 0)
    now = time.time()
    if now - last_refresh >= refresh_rate:
        st.session_state["_app_last_refresh"] = now
        st.rerun()

# ═══════════════════════════════════════════════════════════════════
# Page Routing
# ═══════════════════════════════════════════════════════════════════

page = st.session_state.get("page", "dashboard")

# Title in main area
st.markdown(
    f"""
    <div style="margin-bottom: 0.5rem;">
        <h2 style="color: {theme.text}; margin: 0; font-size: 1.5rem;">
            {'🏠' if page == 'dashboard' else
             '🔍' if page == 'scanner' else
             '📊' if page == 'analyze' else
             '📤' if page == 'upload' else
             '⭐' if page == 'watchlist' else
             '💼' if page == 'portfolio' else
             '📝' if page == 'paper_trading' else
             '🌦️' if page == 'regime' else
             '📈' if page == 'advanced_charts' else
             '📼' if page == 'replay' else
             '📊' if page == 'portfolio_analytics' else
             '⏪' if page == 'backtest' else
             '🎛️' if page == 'backtest_studio' else
             '🏟️' if page == 'strategy_comparison' else
             '⚙️' if page == 'execution_analysis' else
             '🔬' if page == 'optimization_lab' else
             '🧪' if page == 'scenario_lab' else
             '🔍' if page == 'trade_explorer' else
             '📊' if page == 'performance_report' else
             '⚡' if page == 'optimizer' else
             '⚖️' if page == 'portfolio_optimizer' else
             '🧩' if page == 'strategy_builder' else
             '🛒' if page == 'strategy_marketplace' else
             '🧬' if page == 'genetic_optimizer' else
             '🤖' if page == 'ai_advisor' else
             '🧠' if page == 'ml_models' else
             '🎲' if page == 'monte_carlo_lab' else
             '🛡️' if page == 'risk_dashboard' else
             '🏦' if page == 'broker_manager' else
             '🔌' if page == 'plugin_manager' else
             '☁️' if page == 'cloud_sync' else
             '🧪' if page == 'api_explorer' else
             '🔔' if page == 'alerts' else
             '📄' if page == 'reports' else
             '📦' if page == 'cache' else
             '🩺' if page == 'system_status' else
             '⚙️' if page == 'settings' else '📈'}
        </h2>
    </div>
    """,
    unsafe_allow_html=True,
)

try:
    if page == "dashboard":
        from src.ui.pages.dashboard_page import render as render_page
    elif page == "scanner":
        from src.ui.pages.scanner_page import render as render_page
    elif page == "analyze":
        from src.ui.pages.analyze_page import render as render_page
    elif page == "upload":
        from src.ui.pages.upload_page import render as render_page
    elif page == "watchlist":
        from src.ui.pages.watchlist_page import render as render_page
    elif page == "portfolio":
        from src.ui.pages.portfolio_page import render as render_page
    elif page == "paper_trading":
        from src.ui.pages.paper_trading_page import render as render_page
    elif page == "regime":
        from src.ui.pages.market_regime_page import render as render_page
    elif page == "advanced_charts":
        from src.ui.pages.advanced_charts_page import render as render_page
    elif page == "replay":
        from src.ui.pages.replay_page import render as render_page
    elif page == "portfolio_analytics":
        from src.ui.pages.portfolio_analytics_page import render as render_page
    elif page == "backtest":
        from src.ui.pages.backtest_page import render as render_page
    elif page == "backtest_studio":
        from src.ui.pages.backtest_studio_page import render as render_page
    elif page == "strategy_comparison":
        from src.ui.pages.strategy_comparison_page import render as render_page
    elif page == "execution_analysis":
        from src.ui.pages.execution_analysis_page import render as render_page
    elif page == "optimization_lab":
        from src.ui.pages.optimization_lab_page import render as render_page
    elif page == "scenario_lab":
        from src.ui.pages.scenario_lab_page import render as render_page
    elif page == "trade_explorer":
        from src.ui.pages.trade_explorer_page import render as render_page
    elif page == "performance_report":
        from src.ui.pages.performance_report_page import render as render_page
    elif page == "optimizer":
        from src.ui.pages.optimizer_page import render as render_page
    elif page == "portfolio_optimizer":
        from src.ui.pages.portfolio_optimizer_page import render as render_page
    elif page == "strategy_builder":
        from src.ui.pages.strategy_builder_page import render as render_page
    elif page == "strategy_marketplace":
        from src.ui.pages.strategy_marketplace_page import render as render_page
    elif page == "genetic_optimizer":
        from src.ui.pages.genetic_optimizer_page import render as render_page
    elif page == "ai_advisor":
        from src.ui.pages.ai_advisor_page import render as render_page
    elif page == "ml_models":
        from src.ui.pages.ml_models_page import render as render_page
    elif page == "monte_carlo_lab":
        from src.ui.pages.monte_carlo_lab_page import render as render_page
    elif page == "risk_dashboard":
        from src.ui.pages.risk_dashboard_page import render as render_page
    elif page == "broker_manager":
        from src.ui.pages.broker_manager_page import render as render_page
    elif page == "plugin_manager":
        from src.ui.pages.plugin_manager_page import render as render_page
    elif page == "cloud_sync":
        from src.ui.pages.cloud_sync_page import render as render_page
    elif page == "api_explorer":
        from src.ui.pages.api_explorer_page import render as render_page
    elif page == "alerts":
        from src.ui.pages.alerts_page import render as render_page
    elif page == "reports":
        from src.ui.pages.reports_page import render as render_page
    elif page == "cache":
        from src.ui.pages.cache_page import render as render_page
    elif page == "system_status":
        from src.ui.pages.system_status_page import render as render_page
    elif page == "settings":
        from src.ui.pages.settings_page import render as render_page
    else:
        st.error(f"Unknown page: {page}")
        st.session_state["page"] = "dashboard"
        st.rerun()

    render_page()

except Exception as e:
    st.error(
        f"Failed to load page '{page}'. "
        f"Check the logs for details (logs/engine.log)."
    )
    logger = logging.getLogger(__name__)
    logger.error("Page load error for '%s': %s", page, e, exc_info=True)
