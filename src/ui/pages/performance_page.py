"""Portfolio Performance Dashboard — advanced performance metrics and rolling analysis.

Metrics: Daily/Weekly/Monthly/YTD returns, CAGR, Volatility, Sharpe, Sortino,
Calmar, Max Drawdown, Alpha, Beta, Information Ratio, Treynor Ratio.
Rolling: Rolling returns, Rolling Sharpe, Rolling volatility.
Interactive Plotly charts.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
import pandas as pd
import numpy as np

from src.ui.components import kpi_card, line_chart
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
    """Render the Portfolio Performance Dashboard page."""
    section_header("📈 Portfolio Performance", "Advanced performance analytics with rolling metrics")

    service = _svc()

    # ── Load portfolio data ──────────────────────────────────────
    portfolio = st.session_state.get("portfolio", {"holdings": [], "cash": 0.0})
    holdings = portfolio.get("holdings", [])
    trades = st.session_state.get("paper_trades", [])

    if not holdings:
        st.info("No portfolio data available. Populate holdings in the Portfolio page or add paper trades.")
        return

    # ── Calculate live values ────────────────────────────────────
    total_invested = 0.0
    total_current = safe_float(portfolio.get("cash", 0))
    enriched: list[dict[str, Any]] = []

    for h in holdings:
        sym = h.get("symbol", "")
        qty = safe_float(h.get("quantity", 1))
        avg_price = safe_float(h.get("average_price", h.get("avg_price", 0)))
        investment = qty * avg_price
        total_invested += investment

        try:
            quote = service.get_stock(sym)
            live_price = quote.ltp if quote else safe_float(h.get("ltp", 0))
        except Exception:
            live_price = safe_float(h.get("ltp", 0))

        if live_price <= 0:
            live_price = avg_price

        current_val = live_price * qty
        total_current += current_val
        pnl = current_val - investment
        pnl_pct = (pnl / investment * 100) if investment > 0 else 0

        enriched.append({
            "symbol": sym, "qty": qty, "avg_price": avg_price,
            "live_price": live_price, "invested": investment,
            "current_value": current_val, "pnl": pnl, "pnl_pct": pnl_pct,
        })

    total_pnl = total_current - (total_invested + safe_float(portfolio.get("cash", 0)))
    total_return_pct = (total_pnl / (total_invested or 1)) * 100

    # ── Load price history for return calculations ───────────────
    all_returns: list[float] = []
    try:
        for r in enriched:
            hist = service.get_history(r["symbol"], days=365)
            if not hist.is_empty and "Close" in hist.df.columns:
                closes = hist.df["Close"].dropna().values
                if len(closes) > 1:
                    daily_returns = np.diff(closes) / closes[:-1]
                    all_returns.extend(daily_returns.tolist())
        all_returns = all_returns[-252:]  # limit to ~1 year
    except Exception:
        all_returns = []

    # ═══════════════════════════════════════════════════════════════
    # KPI Row
    # ═══════════════════════════════════════════════════════════════
    divider()
    section_header("Key Performance Metrics")

    avg_return = np.mean(all_returns) if all_returns else 0
    std_return = np.std(all_returns) if all_returns else 0
    risk_free = 0.05 / 252  # daily risk-free rate (~5% annual)

    sharpe = ((avg_return - risk_free) / std_return * np.sqrt(252)) if std_return > 0 else 0
    sortino = ((avg_return - risk_free) / np.std([r for r in all_returns if r < 0] or [1]) * np.sqrt(252)) if any(r < 0 for r in all_returns) else 0
    # Max drawdown: maximum peak-to-trough decline using cumulative returns
    max_dd = 0.0
    if all_returns and len(all_returns) > 1:
        try:
            cumulative = (1 + pd.Series(all_returns)).cumprod()
            rolling_max = cumulative.cummax()
            drawdown_series = (cumulative - rolling_max) / rolling_max
            max_dd = float(drawdown_series.min() * 100)
        except Exception:
            max_dd = min(all_returns) * 100 if all_returns else 0
    calmar = ((total_return_pct / 100) / abs(max_dd / 100)) if max_dd != 0 else 0

    cagr = ((1 + total_return_pct / 100) ** (365 / max(len(all_returns), 1)) - 1) * 100 if all_returns else 0
    annual_vol = std_return * np.sqrt(252) * 100

    # Alpha/Beta (using NEPSE index as benchmark)
    alpha = 0.0
    beta = 0.0
    try:
        bench_df = service.get_nepse_index_history(days=500)
        if bench_df is not None and not bench_df.empty and "Close" in bench_df.columns:
            bench_returns = np.diff(bench_df["Close"].values) / bench_df["Close"].values[:-1]
            if len(bench_returns) > 1 and len(all_returns) > 1:
                min_len = min(len(bench_returns), len(all_returns))
                bench_r = bench_returns[-min_len:]
                port_r = np.array(all_returns[-min_len:])
                cov = np.cov(port_r, bench_r)[0, 1]
                var = np.var(bench_r)
                beta = cov / var if var > 0 else 0
                alpha = (avg_return - risk_free - beta * (np.mean(bench_r) - risk_free)) * 252 * 100
    except Exception:
        pass

    cols = st.columns(4)
    with cols[0]:
        kpi_card("📈 Total Return", fmt_pct(total_return_pct / 100.0))
        kpi_card("📊 CAGR", fmt_pct(cagr / 100.0))
    with cols[1]:
        kpi_card("📈 Sharpe", fmt_number(sharpe, 2))
        kpi_card("🎯 Sortino", fmt_number(sortino, 2))
    with cols[2]:
        kpi_card("📉 Max DD", fmt_pct(max_dd / 100.0))
        kpi_card("📊 Calmar", fmt_number(calmar, 2))
    with cols[3]:
        kpi_card("📈 Alpha", fmt_pct(alpha / 100.0))
        kpi_card("📊 Beta", fmt_number(beta, 2))

    cols = st.columns(4)
    with cols[0]:
        kpi_card("📊 Volatility", fmt_pct(annual_vol / 100.0))
    with cols[1]:
        risk_free_annual = 0.05
        treynor = ((total_return_pct / 100 - risk_free_annual) / beta) if beta > 0 else 0
        kpi_card("📈 Treynor", fmt_number(treynor, 2))
    with cols[2]:
        wins = sum(1 for r in enriched if r["pnl"] > 0)
        total = len(enriched) or 1
        win_rate = (wins / total) * 100
        kpi_card("🏆 Win Rate", fmt_pct(win_rate / 100.0))
    with cols[3]:
        info_ratio = alpha / (annual_vol / 100) if annual_vol > 0 else 0
        kpi_card("📊 Info Ratio", fmt_number(info_ratio, 2))

    # ═══════════════════════════════════════════════════════════════
    # Rolling Metrics
    # ═══════════════════════════════════════════════════════════════
    divider()
    section_header("Rolling Metrics", "Rolling returns, Sharpe, and volatility over time")

    if all_returns and len(all_returns) > 20:
        periods = [21, 63, 126, 252]  # 1mo, 3mo, 6mo, 1yr
        selected_period = st.selectbox("Rolling Window", ["21 days (1M)", "63 days (3M)", "126 days (6M)", "252 days (1Y)"], index=1)
        window = int(selected_period.split(" ")[0])

        returns_arr = np.array(all_returns)
        rolling_returns = []
        rolling_sharpe = []
        rolling_vol = []
        dates = list(range(len(returns_arr)))

        for i in range(window, len(returns_arr)):
            segment = returns_arr[i - window : i]
            seg_mean = np.mean(segment) * window
            seg_std = np.std(segment) * np.sqrt(window)
            rolling_returns.append(seg_mean * 100)
            rolling_sharpe.append((seg_mean / seg_std) * np.sqrt(window) if seg_std > 0 else 0)
            rolling_vol.append(seg_std * 100)

        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            fig = make_subplots(
                rows=3, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                subplot_titles=(f"Rolling Returns ({window}d)", f"Rolling Sharpe ({window}d)", f"Rolling Volatility ({window}d)"),
            )

            fig.add_trace(
                go.Scatter(y=rolling_returns, mode="lines", name="Rolling Return",
                           line=dict(color=theme.primary, width=2),
                           fill="tozeroy", fillcolor=f"{theme.primary}22"),
                row=1, col=1,
            )
            fig.add_hline(y=0, line=dict(color=theme.border, width=1, dash="dash"), row=1, col=1)

            fig.add_trace(
                go.Scatter(y=rolling_sharpe, mode="lines", name="Rolling Sharpe",
                           line=dict(color=theme.success, width=2),
                           fill="tozeroy", fillcolor=f"{theme.success}22"),
                row=2, col=1,
            )
            fig.add_hline(y=1, line=dict(color=theme.border, width=1, dash="dash"), row=2, col=1)

            fig.add_trace(
                go.Scatter(y=rolling_vol, mode="lines", name="Rolling Vol",
                           line=dict(color=theme.warning, width=2),
                           fill="tozeroy", fillcolor=f"{theme.warning}22"),
                row=3, col=1,
            )

            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=theme.text),
                height=600,
                hovermode="x unified",
                showlegend=False,
            )
            fig.update_yaxes(gridcolor=theme.border, row=1, col=1)
            fig.update_yaxes(gridcolor=theme.border, row=2, col=1)
            fig.update_yaxes(gridcolor=theme.border, row=3, col=1)

            st.plotly_chart(fig, use_container_width=True)

        except ImportError:
            st.info("Install Plotly for interactive rolling charts")
    else:
        st.info("Insufficient return history for rolling metrics (need 20+ data points)")

    # ═══════════════════════════════════════════════════════════════
    # Holdings Detail
    # ═══════════════════════════════════════════════════════════════
    divider()
    section_header("Holdings Detail")
    rows = []
    for r in enriched:
        rows.append({
            "Symbol": r["symbol"],
            "Invested": fmt_rupees(r["invested"]),
            "Current": fmt_rupees(r["current_value"]),
            "P&L": fmt_rupees(r["pnl"]),
            "P&L %": fmt_pct(r["pnl_pct"] / 100.0),
            "Weight": fmt_pct(r["current_value"] / max(total_current - safe_float(portfolio.get("cash", 0)), 1)),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
