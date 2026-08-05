"""Trade Explorer — inspect closed trades and trade distributions (Sprint 9, Part 9.12)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.models import BacktestConfig, Order, OrderSide
from src.ui.helpers import section_header, divider, fmt_number, empty_state
from src.ui.theme import theme


def _strategy(symbol: str, qty: int):
    """Strategy that buys early and exits with a bracket."""
    entered = {"flag": False}

    def strategy(bar_index: int, bars: dict[str, Any], ctx: Any) -> list[Order]:
        from src.backtesting.orders import OrderManager
        manager: OrderManager = ctx["order_manager"]
        bar = bars.get(symbol)
        if bar is None or bar.close <= 0 or entered["flag"]:
            return []
        entered["flag"] = True
        price = bar.close
        return [
            manager.market(symbol, OrderSide.BUY, qty),
            manager.limit(symbol, OrderSide.SELL, qty, price * 1.12),
            manager.stop(symbol, OrderSide.SELL, qty, price * 0.92),
        ]

    return strategy


def _run(qty: int, symbol: str) -> BacktestResult:
    """Run a deterministic demo backtest producing trades."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(11)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=180, freq="B")
    close = 400.0 + np.cumsum(rng.normal(0.6, 2.0, 180))
    close = np.maximum(close, 50.0)
    open_ = close + rng.normal(0, 0.4, 180)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1.0, 180))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1.0, 180))
    volume = rng.integers(40_000, 700_000, 180)
    df = pd.DataFrame({"Date": dates, "Open": open_, "High": high,
                       "Low": low, "Close": close, "Volume": volume})
    config = BacktestConfig(initial_cash=2_000_000, trades_per_year=252)
    engine = BacktestEngine(config=config, strategy=_strategy(symbol, qty))
    return engine.run({symbol: df})


def render() -> None:
    """Render the Trade Explorer page."""
    section_header("🔍 Trade Explorer", "Inspect closed trades, exit reasons, and P&L distributions")

    col1, col2 = st.columns(2)
    with col1:
        qty = st.number_input("Quantity", min_value=1, value=500, step=100, key="te_qty")
    with col2:
        symbol = st.selectbox("Symbol", ["NABIL", "ADBL", "PCBL", "NRIC", "NTC", "CHCL", "EBL", "SHL"], key="te_symbol")

    if st.button("▶️ Generate Trades", type="primary", key="te_run"):
        with st.spinner("Running..."):
            result = _run(qty, symbol)
        st.session_state["te_result"] = result
        _render(result)

    elif "te_result" in st.session_state:
        _render(st.session_state["te_result"])


def _render(result: BacktestResult) -> None:
    """Render the trades table and distributions."""
    divider()

    trades = result.trades
    if not trades:
        empty_state("🫙", "No closed trades", "Try a different quantity or symbol.")
        return

    section_header(f"📋 Closed Trades ({len(trades)})")
    rows = [t.to_dict() for t in trades]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Exit reason distribution
    from collections import Counter
    reasons = Counter(t.exit_reason for t in trades)

    c1, c2 = st.columns(2)
    with c1:
        try:
            import plotly.graph_objects as go
            fig = go.Figure(go.Bar(
                x=list(reasons.keys()), y=list(reasons.values()),
                marker_color=theme.chart_colors,
            ))
            fig.update_layout(
                title="Exit Reason Distribution", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", font=dict(color=theme.text),
                height=320, margin=dict(l=40, r=20, t=50, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.write(dict(reasons))

    with c2:
        # P&L histogram
        pnls = [t.net_pnl for t in trades]
        try:
            import plotly.graph_objects as go
            fig2 = go.Figure(go.Histogram(
                x=pnls, nbinsx=15, marker_color=theme.chart_colors[1],
            ))
            fig2.update_layout(
                title="Net P&L Distribution", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", font=dict(color=theme.text),
                height=320, margin=dict(l=40, r=20, t=50, b=40),
            )
            st.plotly_chart(fig2, use_container_width=True)
        except ImportError:
            st.line_chart(sorted(pnls))

    # Aggregate
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Gross P&L", fmt_number(sum(t.gross_pnl for t in trades)))
    with c2:
        st.metric("Net P&L", fmt_number(sum(t.net_pnl for t in trades)))
    with c3:
        st.metric("Avg Hold (bars)", f"{sum(t.holding_bars for t in trades) / len(trades):.1f}")
