"""Portfolio page — holdings with live pricing, allocation pie, and performance."""

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
    safe_get,
    safe_float,
    fmt_number,
)
from src.ui.theme import theme
from src.data import DataService as _DataService


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the Portfolio page with live pricing for holdings."""
    section_header("Portfolio", "Holdings overview, allocation, and performance")

    # ── Refresh ──────────────────────────────────────────────────
    col1, col2 = st.columns([6, 1])
    with col2:
        refresh = st.button("🔄 Refresh", use_container_width=True)

    divider()

    # ── Load portfolio from session state ────────────────────────
    portfolio = st.session_state.get("portfolio", {"holdings": [], "cash": 0.0})
    if refresh:
        st.session_state["portfolio_last_refresh"] = None

    holdings = portfolio.get("holdings", [])
    cash = safe_float(portfolio.get("cash", 0))

    if not holdings:
        st.info("No portfolio data. Populate holdings from the settings or saved state.")
        return

    # ── Calculate live values ────────────────────────────────────
    with st.spinner("Updating live prices..."):
        total_invested = 0.0
        total_book_value = 0.0
        live_rows: list[dict[str, Any]] = []
        errors = 0

        for h in holdings:
            sym = h.get("symbol", "")
            qty = safe_float(h.get("quantity", 0))
            avg_price = safe_float(h.get("average_price", h.get("avg_price", 0)))
            investment = qty * avg_price
            total_invested += investment

            # Try live price via DataService
            live_price = 0.0
            try:
                quote = _svc().get_stock(sym)
                if quote:
                    live_price = quote.ltp
                else:
                    live_price = safe_float(h.get("ltp", h.get("current_price", 0)))
            except Exception:
                live_price = safe_float(h.get("ltp", h.get("current_price", 0)))

            current_value = live_price * qty if live_price > 0 else investment
            total_book_value += current_value
            pnl = current_value - investment
            pnl_pct = (pnl / investment * 100) if investment > 0 else 0
            if live_price == 0:
                errors += 1

            live_rows.append({
                "symbol": sym,
                "qty": qty,
                "avg_price": avg_price,
                "live_price": live_price,
                "investment": investment,
                "current_value": current_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "weight": 0.0,  # compute below
            })

        total_value = cash + total_book_value
        for r in live_rows:
            r["weight"] = (r["current_value"] / total_book_value * 100) if total_book_value > 0 else 0

        pnl_total = total_book_value - total_invested
        pnl_pct_total = (pnl_total / total_invested * 100) if total_invested > 0 else 0

    # ── Summary cards ────────────────────────────────────────────
    cols = st.columns(5)
    with cols[0]:
        kpi_card("💰 Cash", fmt_rupees(cash))
    with cols[1]:
        kpi_card("📈 Invested", fmt_rupees(total_invested))
    with cols[2]:
        kpi_card("💵 Current Value", fmt_rupees(total_value),
                 delta=fmt_rupees(pnl_total))
    with cols[3]:
        kpi_card("📊 P&L", fmt_rupees(pnl_total),
                 delta=fmt_pct(pnl_pct_total / 100.0))
    with cols[4]:
        wins = sum(1 for r in live_rows if r["pnl"] > 0)
        total = len(live_rows) or 1
        kpi_card("🏆 Win Rate", fmt_pct((wins / total) / 100.0))

    if errors:
        st.caption(f"⚠️ {errors} holdings had no live price data — using book value")

    divider()

    # ── Holdings table ───────────────────────────────────────────
    section_header("Holdings")
    rows = []
    for r in live_rows:
        rows.append({
            "Symbol": r["symbol"],
            "Qty": f"{r['qty']:.0f}",
            "Avg Price": fmt_rupees(r["avg_price"]),
            "LTP": fmt_rupees(r["live_price"]),
            "Investment": fmt_rupees(r["investment"]),
            "Current Value": fmt_rupees(r["current_value"]),
            "P&L": fmt_rupees(r["pnl"]),
            "P&L %": fmt_pct(r["pnl_pct"] / 100.0),
            "Alloc %": fmt_pct(r["weight"] / 100.0),
        })

    df = pd.DataFrame(rows)

    def color_pnl(val: str) -> str:
        if val.startswith("₹ -") or val.startswith("-"):
            return "color: #FF5252"
        if val.startswith("₹") and not val.startswith("₹ -"):
            return "color: #00C853"
        return ""

    st.dataframe(
        df.style.applymap(color_pnl, subset=["P&L", "P&L %"]),
        use_container_width=True,
        hide_index=True,
    )

    divider()

    # ── Allocation Pie + Performance ─────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        labels = [r["symbol"] for r in live_rows]
        values = [r["current_value"] for r in live_rows]
        if values and any(v > 0 for v in values):
            pie_chart(labels, values, title="Portfolio Allocation", height=350)
        else:
            st.info("No allocation data")

    with col2:
        pnl_vals = [r["pnl_pct"] for r in live_rows]
        labels_pnl = [r["symbol"] for r in live_rows]
        if pnl_vals:
            import plotly.express as px
            fig = px.bar(
                x=labels_pnl, y=pnl_vals,
                title="P&L % by Holding",
                color=pnl_vals,
                color_continuous_scale=["#FF5252", "#FFC107", "#00C853"],
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FFFFFF"),
                xaxis=dict(gridcolor="#2D3138"),
                yaxis=dict(gridcolor="#2D3138"),
                height=350,
            )
            st.plotly_chart(fig, use_container_width=True)
