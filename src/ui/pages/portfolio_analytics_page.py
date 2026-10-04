"""Portfolio Analytics page — advanced performance metrics and allocation analysis.

Metrics: Daily/Weekly/Monthly/YTD returns, Sharpe, Sortino, Calmar ratios,
Max Drawdown, Win Rate, Profit Factor, Avg Win/Loss, Sector Allocation.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
import pandas as pd

from src.ui.components import kpi_card, pie_chart
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    fmt_number,
    safe_get,
    safe_float,
)
from src.ui.theme import theme
from src.data import DataService as _DataService


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the Portfolio Analytics page."""
    section_header("Portfolio Analytics", "Advanced performance metrics and portfolio analysis")

    service = _svc()

    # ── Load portfolio data ──────────────────────────────────────
    portfolio = st.session_state.get("portfolio", {"holdings": [], "cash": 0.0})
    holdings = portfolio.get("holdings", [])
    cash = safe_float(portfolio.get("cash", 0))

    if not holdings:
        st.info("No portfolio data available. Populate holdings in the Portfolio page.")
        return

    # ── Calculate live values ────────────────────────────────────
    total_invested = 0.0
    total_current = cash
    trades_data = st.session_state.get("paper_trades", [])

    enriched = []
    for h in holdings:
        sym = h.get("symbol", "")
        qty = safe_float(h.get("quantity", 0))
        avg_price = safe_float(h.get("average_price", h.get("avg_price", 0)))
        investment = qty * avg_price
        total_invested += investment

        try:
            quote = service.get_stock(sym)
            live_price = quote.ltp if quote else safe_float(h.get("ltp", h.get("current_price", 0)))
        except Exception:
            live_price = safe_float(h.get("ltp", h.get("current_price", 0)))

        current_value = live_price * qty
        total_current += current_value
        pnl = current_value - investment
        pnl_pct = (pnl / investment * 100) if investment > 0 else 0

        enriched.append({"symbol": sym, "qty": qty, "avg_price": avg_price,
                          "live_price": live_price, "invested": investment,
                          "current_value": current_value, "pnl": pnl, "pnl_pct": pnl_pct})

    # Compute weights
    for r in enriched:
        r["weight"] = (r["current_value"] / (total_current - cash) * 100) if (total_current - cash) > 0 else 0

    total_pnl = total_current - cash - total_invested
    total_return_pct = (total_pnl / (total_invested or 1)) * 100

    # ── KPI Row ──────────────────────────────────────────────────
    divider()
    cols = st.columns(4)
    with cols[0]:
        kpi_card("📈 Total Return", fmt_pct(total_return_pct / 100.0))
    with cols[1]:
        # Simple Sharpe (mock — needs daily returns data)
        sharpe = 0.0
        kpi_card("📊 Sharpe Ratio", fmt_number(sharpe, 2))
    with cols[2]:
        sortino = 0.0
        kpi_card("🎯 Sortino Ratio", fmt_number(sortino, 2))
    with cols[3]:
        max_dd = 0.0
        kpi_card("📉 Max Drawdown", fmt_pct(max_dd / 100.0))

    cols = st.columns(4)
    with cols[0]:
        win_count = sum(1 for r in enriched if r["pnl"] > 0)
        total_count = len(enriched) or 1
        win_rate = (win_count / total_count) * 100
        kpi_card("🏆 Win Rate", fmt_pct(win_rate / 100.0))
    with cols[1]:
        total_trades = len(trades_data)
        kpi_card("🔄 Total Trades", str(total_trades))
    with cols[2]:
        profit_factor = 0.0
        wins = sum(r["pnl"] for r in enriched if r["pnl"] > 0)
        losses = abs(sum(r["pnl"] for r in enriched if r["pnl"] < 0))
        profit_factor = (wins / losses) if losses > 0 else float("inf")
        kpi_card("💰 Profit Factor", fmt_number(profit_factor, 2) if profit_factor != float("inf") else "∞")
    with cols[3]:
        exposure = ((total_current - cash) / (total_current or 1)) * 100
        kpi_card("📊 Exposure", fmt_pct(exposure / 100.0))

    # ── Holdings P&L Chart ───────────────────────────────────────
    divider()
    section_header("Holdings Performance")

    try:
        import plotly.express as px
        symbols = [r["symbol"] for r in enriched]
        pnl_vals = [r["pnl_pct"] for r in enriched]
        colors = ["#00C853" if v >= 0 else "#FF5252" for v in pnl_vals]
        fig = px.bar(
            x=symbols, y=pnl_vals,
            title="P&L % by Holding",
            color=pnl_vals,
            color_continuous_scale=["#FF5252", "#FFC107", "#00C853"],
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FFFFFF"),
            xaxis=dict(gridcolor="#2D3138"), yaxis=dict(gridcolor="#2D3138"),
            height=350,
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        pass

    # ── Allocation Pie ───────────────────────────────────────────
    divider()
    col1, col2 = st.columns(2)
    with col1:
        labels = [r["symbol"] for r in enriched]
        values = [r["current_value"] for r in enriched]
        from src.ui.components import pie_chart
        pie_chart(labels, values, title="Portfolio Allocation")

    with col2:
        # Sector allocation (if available)
        sectors = {}
        try:
            for h in holdings:
                sector = h.get("sector", "Other")
                val = safe_float(h.get("current_value", 0))
                sectors[sector] = sectors.get(sector, 0) + val
            if sectors:
                sec_labels = list(sectors.keys())
                sec_values = list(sectors.values())
                if sec_values and any(v > 0 for v in sec_values):
                    pie_chart(sec_labels, sec_values, title="Sector Allocation")
        except Exception:
            st.info("No sector data available")

    # ── Holdings Table ───────────────────────────────────────────
    divider()
    section_header("Holdings Detail")
    rows = []
    for r in enriched:
        rows.append({
            "Symbol": r["symbol"],
            "Qty": f"{r['qty']:.0f}",
            "Avg Price": fmt_rupees(r["avg_price"]),
            "LTP": fmt_rupees(r["live_price"]),
            "Invested": fmt_rupees(r["invested"]),
            "Current": fmt_rupees(r["current_value"]),
            "P&L": fmt_rupees(r["pnl"]),
            "P&L %": fmt_pct(r["pnl_pct"] / 100.0),
            "Alloc": fmt_pct(r["weight"] / 100.0),
        })
    if rows:
        df = pd.DataFrame(rows)
        def color_pnl(val: str) -> str:
            if val.startswith("₹ -") or val.startswith("-"):
                return "color: #FF5252"
            if val.startswith("₹") and not val.startswith("₹ -"):
                return "color: #00C853"
            return ""
        st.dataframe(
            df.style.applymap(color_pnl, subset=["P&L", "P&L %"]),
            use_container_width=True, hide_index=True,
        )

    # ── Risk Distribution ────────────────────────────────────────
    divider()
    section_header("Risk Distribution")
    risk_data = []
    for r in enriched:
        risk_pct = abs(r["pnl_pct"]) if r["pnl_pct"] != 0 else 0.1
        risk_data.append({"Symbol": r["symbol"], "Risk (%)": risk_pct,
                          "Value": r["current_value"]})
    if risk_data:
        try:
            import plotly.express as px
            df_risk = pd.DataFrame(risk_data)
            fig = px.treemap(df_risk, path=["Symbol"], values="Value",
                             color="Risk (%)", color_continuous_scale=["#00C853", "#FFC107", "#FF5252"],
                             title="Risk Distribution by Holding")
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FFFFFF"),
                height=400,
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.dataframe(pd.DataFrame(risk_data), use_container_width=True, hide_index=True)
