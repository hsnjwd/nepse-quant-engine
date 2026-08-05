"""Dashboard page — professional trading terminal with live market snapshot, breadth, and portfolio overview."""

from __future__ import annotations

import time
from typing import Any

import streamlit as st
import pandas as pd

from src.ui.components import kpi_card, regime_badge, signal_badge, pie_chart, line_chart
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    safe_get,
    safe_float,
    fmt_number,
)
from src.ui.theme import theme
from src.data import DataService as _DataService


def _svc() -> _DataService:
    """Get or create the DataService singleton."""
    return _DataService()


def render() -> None:
    """Render the professional Dashboard page with live NEPSE market data."""
    section_header("Dashboard", "Live NEPSE market snapshot, market breadth, top movers, and portfolio overview")

    service = _svc()

    # ═══════════════════════════════════════════════════════════════
    # SECTION 0 — Quick navigation (AI platform integration)
    # ═══════════════════════════════════════════════════════════════
    q1, q2, q3, q4 = st.columns(4)
    with q1:
        if st.button("🤖 AI Advisor", use_container_width=True):
            st.session_state["page"] = "ai_advisor"
            st.rerun()
    with q2:
        if st.button("🧠 ML Models", use_container_width=True):
            st.session_state["page"] = "ml_models"
            st.rerun()
    with q3:
        if st.button("🛡️ Risk Dashboard", use_container_width=True):
            st.session_state["page"] = "risk_dashboard"
            st.rerun()
    with q4:
        if st.button("⚖️ Portfolio Optimizer", use_container_width=True):
            st.session_state["page"] = "portfolio_optimizer"
            st.rerun()
    st.caption("Quick links to the AI & quant platform.")

    # ═══════════════════════════════════════════════════════════════
    # SECTION 1 — Market Snapshot
    # ═══════════════════════════════════════════════════════════════
    divider()
    section_header("📊 Market Snapshot", "Live NEPSE index and market activity")

    summary = service.get_market_summary()
    if summary and summary.index > 0:
        cols = st.columns(7)
        with cols[0]:
            kpi_card("📈 NEPSE Index", f"{summary.index:,.2f}",
                     delta=f"{summary.change:+.2f} ({summary.change_pct:+.2f}%)")
        with cols[1]:
            kpi_card("📊 Volume", fmt_number(summary.volume))
        with cols[2]:
            kpi_card("💰 Turnover", fmt_rupees(summary.turnover))
        with cols[3]:
            kpi_card("🟢 Advances", str(summary.advances))
        with cols[4]:
            kpi_card("🔴 Declines", str(summary.declines))
        with cols[5]:
            kpi_card("⚪ Unchanged", str(summary.unchanged))

        with cols[6]:
            status_color = theme.success if summary.status == "Open" else theme.danger
            st.markdown(
                f"""
                <div style="text-align: center;">
                    <p style="color: {theme.text_secondary}; font-size: 0.85rem; margin-bottom: 2px;">Market</p>
                    <p style="color: {status_color}; font-size: 1.1rem; font-weight: 700;">
                        {'🟢' if summary.status == 'Open' else '🔴'} {summary.status}
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Market breadth
        if summary.advances > 0 or summary.declines > 0:
            advance_decline_ratio = summary.advances / max(summary.declines, 1)
            breadth_color = theme.success if advance_decline_ratio >= 1.0 else theme.danger
            breadth_label = "Bullish" if advance_decline_ratio >= 1.5 else (
                "Neutral" if advance_decline_ratio >= 0.8 else "Bearish"
            )
            st.markdown(
                f"""
                <div style="
                    background: {theme.card_bg};
                    border-radius: 8px;
                    padding: 0.75rem 1rem;
                    margin-top: 0.5rem;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                ">
                    <span style="color: {theme.text_secondary};">📊 Market Breadth</span>
                    <span style="color: {breadth_color}; font-weight: 600;">
                        A/D: {advance_decline_ratio:.2f} — {breadth_label}
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("🌐 Live market data unavailable. Showing last cached data.")

    # ═══════════════════════════════════════════════════════════════
    # SECTION 2 — Top Gainers / Losers / Turnover
    # ═══════════════════════════════════════════════════════════════
    divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("##### 🟢 Top Gainers")
        gainers = service.get_top_gainers(8)
        if gainers:
            g_rows = []
            for s in gainers:
                g_rows.append({
                    "Symbol": s.symbol,
                    "LTP": fmt_rupees(s.ltp),
                    "Change": f"<span style='color: #00C853;'>+{s.change_pct:.2f}%</span>",
                })
            df_g = pd.DataFrame(g_rows)
            st.write(df_g.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.caption("No data")
    with col2:
        st.markdown("##### 🔴 Top Losers")
        losers = service.get_top_losers(8)
        if losers:
            l_rows = []
            for s in losers:
                l_rows.append({
                    "Symbol": s.symbol,
                    "LTP": fmt_rupees(s.ltp),
                    "Change": f"<span style='color: #FF5252;'>{s.change_pct:.2f}%</span>",
                })
            df_l = pd.DataFrame(l_rows)
            st.write(df_l.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.caption("No data")
    with col3:
        st.markdown("##### 💰 Top Turnover")
        turnovers = service.get_top_turnover(8)
        if turnovers:
            t_rows = []
            for s in turnovers:
                t_rows.append({
                    "Symbol": s.symbol,
                    "LTP": fmt_rupees(s.ltp),
                    "Volume": fmt_number(s.volume),
                })
            df_t = pd.DataFrame(t_rows)
            st.write(df_t.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.caption("No data")

    # ═══════════════════════════════════════════════════════════════
    # SECTION 3 — Market Scan / Signals
    # ═══════════════════════════════════════════════════════════════
    divider()
    section_header("🔍 Market Scan", "Live scan results across all stocks")
    scan_result = service.scan_market()
    if scan_result and scan_result.results:
        results = scan_result.results
        cols = st.columns(3)
        with cols[0]:
            kpi_card("🟢 Buy Signals", str(scan_result.buy_count))
        with cols[1]:
            kpi_card("🔴 Sell Signals", str(scan_result.sell_count))
        with cols[2]:
            kpi_card("🟡 Hold", str(scan_result.hold_count))

        # Top signals table
        top = sorted(results, key=lambda r: safe_float(safe_get(r, "score", 0)), reverse=True)[:8]
        st.markdown("#### 🏆 Top Signals")
        rows = []
        for r in top:
            rows.append({
                "Symbol": safe_get(r, "symbol", "—"),
                "Signal": safe_get(r, "signal", "HOLD"),
                "Score": fmt_number(safe_float(safe_get(r, "score", 0))),
                "Confidence": fmt_pct(safe_float(safe_get(r, "confidence", 0)) / 100.0),
                "Price": fmt_rupees(safe_float(safe_get(r, "live_price", safe_get(r, "price", 0)))),
            })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("🌐 Live scan unavailable. Run full scan from the Scanner page.")

    # ═══════════════════════════════════════════════════════════════
    # SECTION 4 — Market Regime
    # ═══════════════════════════════════════════════════════════════
    _render_regime_section(service)

    # ═══════════════════════════════════════════════════════════════
    # SECTION 5 — Portfolio Summary
    # ═══════════════════════════════════════════════════════════════
    _render_portfolio_section(service)

    # ═══════════════════════════════════════════════════════════════
    # SECTION 6 — Recent Trades
    # ═══════════════════════════════════════════════════════════════
    _render_recent_trades()

    # ═══════════════════════════════════════════════════════════════
    # SECTION 7 — Recent Alerts
    # ═══════════════════════════════════════════════════════════════
    _render_recent_alerts()


# ── Section renderers ─────────────────────────────────────────────


def _render_regime_section(service: _DataService) -> None:
    """Render the current market regime card with confidence and reasons."""
    divider()
    section_header("🌦️ Market Regime", "Current market regime detected from NEPSE index")
    try:
        df = service.get_nepse_index_history(days=500)
        if df is not None and not df.empty:
            from src.regime.detector import MarketRegimeDetector
            detector = MarketRegimeDetector()
            result = detector.detect(df)
            if result:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    regime_badge(result.regime)
                    st.markdown(
                        f"<p style='text-align: center; color: {theme.text_secondary};'>"
                        f"Confidence: {result.confidence:.1f}%</p>",
                        unsafe_allow_html=True,
                    )
                if result.reasons:
                    st.markdown("##### Reasons")
                    for reason in result.reasons:
                        st.markdown(f"- {reason}")
                # Metrics row
                if hasattr(result, "metrics") and result.metrics:
                    metrics = result.metrics
                    cols = st.columns(min(len(metrics), 6))
                    for i, (k, v) in enumerate(list(metrics.items())[:6]):
                        with cols[i]:
                            val_str = f"{v:.2f}" if isinstance(v, float) else str(v)
                            st.metric(k.replace("_", " ").title(), val_str)
    except Exception as exc:
        st.info(f"ℹ️ Regime detection unavailable: {exc}")


def _render_portfolio_section(service: _DataService) -> None:
    """Render the portfolio summary from session state."""
    divider()
    section_header("💼 Portfolio Summary")

    portfolio = st.session_state.get("portfolio", {"holdings": [], "cash": 0.0})
    holdings = portfolio.get("holdings", [])
    cash = safe_float(portfolio.get("cash", 0))

    if not holdings:
        st.info("No portfolio data. Add holdings in the Portfolio page or load from saved state.")
        return

    total_invested = sum(safe_float(h.get("investment", 0)) for h in holdings)

    # Calculate live portfolio value using DataService
    live_value = cash
    try:
        for h in holdings:
            sym = h.get("symbol", "")
            quote = service.get_stock(sym)
            qty = safe_float(h.get("quantity", 0))
            if quote:
                live_value += quote.ltp * qty
            else:
                live_value += safe_float(h.get("current_value", 0))
    except Exception:
        live_value = cash + sum(safe_float(h.get("current_value", 0)) for h in holdings)

    pnl = live_value - (cash + total_invested)
    pnl_pct = (pnl / (cash + total_invested)) * 100 if (cash + total_invested) > 0 else 0
    win_rate = 0
    if holdings:
        wins = sum(1 for h in holdings if safe_float(h.get("pnl", 0)) > 0)
        win_rate = (wins / len(holdings)) * 100

    cols = st.columns(5)
    with cols[0]:
        kpi_card("💰 Cash", fmt_rupees(cash))
    with cols[1]:
        kpi_card("📈 Invested", fmt_rupees(total_invested))
    with cols[2]:
        kpi_card("💵 Current Value", fmt_rupees(live_value),
                 delta=fmt_rupees(pnl))
    with cols[3]:
        kpi_card("📊 Return", fmt_pct(pnl_pct / 100.0))
    with cols[4]:
        kpi_card("🏆 Win Rate", fmt_pct(win_rate / 100.0))

    # Allocation pie
    if holdings:
        labels = [h.get("symbol", "?") for h in holdings]
        values = [safe_float(h.get("current_value", 0)) for h in holdings if safe_float(h.get("current_value", 0)) > 0]
        if values:
            pie_chart(labels, values, title="Portfolio Allocation")


def _render_recent_trades() -> None:
    """Render the most recent paper trades."""
    divider()
    section_header("📝 Recent Trades")
    trades = st.session_state.get("paper_trades", [])
    if trades:
        recent = trades[-8:]
        st.dataframe(
            [{"Date": t.get("date", "—"), "Type": t.get("type", "—"),
              "Symbol": t.get("symbol", "—"), "Qty": t.get("qty", 0),
              "Price": fmt_rupees(safe_float(t.get("price", 0))),
              "Total": fmt_rupees(safe_float(t.get("total", 0)))}
             for t in recent],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No recent trades. Use Paper Trading to simulate trades.")


def _render_recent_alerts() -> None:
    """Render the most recent alerts."""
    divider()
    section_header("🔔 Recent Alerts")

    alerts = st.session_state.get("alerts", [])
    if not alerts:
        try:
            from src.alerts.engine import get_alerts_history
            if callable(get_alerts_history):
                alerts = get_alerts_history()[:10]
        except Exception:
            pass

    if alerts:
        recent = alerts[:10]
        st.dataframe(
            [
                {"Date": a.get("date", "—"), "Symbol": a.get("symbol", "—"), "Message": a.get("message", "—")}
                for a in recent
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No recent alerts.")
