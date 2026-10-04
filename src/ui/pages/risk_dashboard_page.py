"""Risk Dashboard page — portfolio risk metrics and concentration."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider, fmt_pct, fmt_rupees, safe_float
from src.ui.components import kpi_card, pie_chart


def render() -> None:
    """Render the Risk Dashboard page."""
    section_header(
        "🛡️ Risk Dashboard",
        "Portfolio risk, drawdown, exposure, and concentration",
    )

    tabs = st.tabs(["📊 Overview", "💹 Drawdown", "🧮 Risk Lab"])

    # ── Tab 1: Overview ───────────────────────────────────────────
    with tabs[0]:
        st.caption("Analyze risk from your holdings.")
        col_btn, col_hint = st.columns([1, 3])
        with col_btn:
            if st.button("💼 Load from Portfolio", key="rd_load_portfolio"):
                _load_portfolio_holdings()
        with col_hint:
            st.caption("Pulls current holdings from the Portfolio page.")
        holdings_text = st.text_area(
            "Holdings (symbol,qty,price per line)",
            value=st.session_state.get("rd_holdings_input", "NABIL,100,500\nNRIC,50,350\nNTC,25,800\nADBL,40,300"),
            key="rd_holdings_input",
        )
        cash = st.number_input("Cash", value=100_000.0, key="rd_cash")

        if st.button("Compute Risk Overview", type="primary", key="rd_run"):
            holdings: list[dict[str, Any]] = []
            for line in holdings_text.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    holdings.append(
                        {
                            "symbol": parts[0],
                            "quantity": safe_float(parts[1]),
                            "current_price": safe_float(parts[2]),
                        }
                    )
            if not holdings:
                st.warning("Enter at least one holding.")
                return

            values = [h["quantity"] * h["current_price"] for h in holdings]
            invested = sum(values)
            total = invested + cash
            weights = [v / invested if invested else 0 for v in values]
            top_weight = max(weights) if weights else 0.0

            st.session_state["rd_holdings"] = holdings
            st.session_state["rd_total"] = total
            st.session_state["rd_weights"] = weights

            # Part 7 — risk alert notification (one-shot, in handler)
            if top_weight > 0.4:
                try:
                    from src.ui.notifications import notification_manager

                    notification_manager.notify_risk(
                        "Concentration risk detected",
                        message=(
                            f"Top holding is {top_weight * 100:.0f}% of "
                            f"invested capital — consider rebalancing."
                        ),
                    )
                except Exception:
                    pass

        holdings = st.session_state.get("rd_holdings")
        if holdings:
            total = st.session_state["rd_total"]
            weights = st.session_state["rd_weights"]
            values = [h["quantity"] * h["current_price"] for h in holdings]

            cols = st.columns(4)
            with cols[0]:
                kpi_card("Invested", fmt_rupees(sum(values)))
            with cols[1]:
                kpi_card("Total Value", fmt_rupees(total))
            with cols[2]:
                kpi_card("Cash Ratio", fmt_pct(cash / total if total else 0))
            with cols[3]:
                kpi_card("Top Weight", fmt_pct(max(weights) if weights else 0))

            pie_chart(
                [h["symbol"] for h in holdings],
                values,
                title="Allocation",
                height=350,
            )

            if max(weights) > 0.4:
                st.warning(
                    f"⚠️ Concentration risk: top holding is "
                    f"{max(weights) * 100:.0f}% of invested capital."
                )

    # ── Tab 2: Drawdown ───────────────────────────────────────────
    with tabs[1]:
        symbol = st.text_input("Symbol", value="NABIL", key="rd_symbol")
        if st.button("Show Drawdown", key="rd_dd_btn"):
            try:
                from src.data import DataService

                history = DataService().get_history(symbol, days=365)
                if history.is_empty:
                    st.warning(f"No history available for {symbol}.")
                    return
                closes = history.df["Close"]
                peak = closes.cummax()
                drawdown = (closes - peak) / peak
                st.session_state["rd_dd"] = drawdown
                st.session_state["rd_dd_dates"] = history.df.index
                kpi_card(
                    "Max Drawdown",
                    fmt_pct(float(drawdown.min())),
                )
            except Exception as exc:
                st.error(f"Failed: {exc}")

        dd = st.session_state.get("rd_dd")
        if dd is not None:
            try:
                import plotly.graph_objects as go

                dates = st.session_state.get("rd_dd_dates", list(range(len(dd))))
                fig = go.Figure(
                    go.Scatter(x=dates, y=dd * 100, fill="tozeroy", line=dict(color="#FF5252"))
                )
                fig.update_layout(
                    yaxis_title="Drawdown %",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                st.info(f"Chart unavailable: {exc}")

    # ── Tab 3: Risk lab ───────────────────────────────────────────
    with tabs[2]:
        st.caption("Compute VaR / CVaR from returns (paste or demo).")
        if st.button("Open Monte Carlo Lab", key="rd_lab_link"):
            st.session_state["page"] = "monte_carlo_lab"
            st.rerun()
        st.info(
            "Full VaR, CVaR, stress testing and ruin analysis are "
            "available on the **Monte Carlo Lab** page."
        )


def _load_portfolio_holdings() -> None:
    """Load holdings from the Portfolio session state into the risk view."""
    portfolio = st.session_state.get("portfolio", {"holdings": []})
    holdings = portfolio.get("holdings", [])
    if not holdings:
        st.warning("No holdings in the Portfolio page yet.")
        return
    lines = []
    for h in holdings:
        symbol = h.get("symbol", "")
        if not symbol:
            continue
        qty = safe_float(h.get("quantity", h.get("qty", 0)))
        price = safe_float(
            h.get("current_price") or h.get("average_price") or h.get("ltp")
        )
        if not price and h.get("current_value") and qty > 0:
            price = safe_float(h.get("current_value")) / qty
        lines.append(f"{symbol},{qty:g},{price:.2f}")
    if lines:
        st.session_state["rd_holdings_input"] = "\n".join(lines)
        st.rerun()

