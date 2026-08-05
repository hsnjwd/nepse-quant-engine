"""Execution Analysis — fills, order lifecycle, and cost analysis (Sprint 9, Part 9.12)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.models import BacktestConfig, Order, OrderSide
from src.ui.helpers import section_header, divider, fmt_number, empty_state
from src.ui.theme import theme


def _demo_strategy(symbol: str, qty: int):
    """Strategy that buys at open bar 1 and sells at the last bar."""
    entered = {"flag": False}

    def strategy(bar_index: int, bars: dict[str, Any], ctx: Any) -> list[Order]:
        from src.backtesting.orders import OrderManager
        manager: OrderManager = ctx["order_manager"]
        bar = bars.get(symbol)
        if bar is None or bar.close <= 0:
            return []
        if not entered["flag"]:
            entered["flag"] = True
            return [
                manager.market(symbol, OrderSide.BUY, qty),
                manager.stop(symbol, OrderSide.SELL, qty, bar.close * 0.95),
                manager.limit(symbol, OrderSide.SELL, qty, bar.close * 1.15),
            ]
        return []

    return strategy


def render() -> None:
    """Render the Execution Analysis page."""
    section_header("⚙️ Execution Analysis", "Order lifecycle, fills, and cost breakdown")

    col1, col2, col3 = st.columns(3)
    with col1:
        slippage = st.selectbox("Slippage Model", ["fixed", "percentage", "volume", "volatility", "spread", "random"], key="ex_slip")
    with col2:
        commission = st.selectbox("Commission Model", ["flat", "percentage", "tiered", "broker"], key="ex_comm")
    with col3:
        qty = st.number_input("Quantity", min_value=1, value=1000, step=100, key="ex_qty")

    if st.button("▶️ Run Execution", type="primary", key="ex_run"):
        result = _run(slippage, commission, qty)
        st.session_state["execution_result"] = result
        _render(result)

    elif "execution_result" in st.session_state:
        _render(st.session_state["execution_result"])


def _run(slippage: str, commission: str, qty: int) -> BacktestResult:
    """Run a deterministic demo execution analysis."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(7)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=120, freq="B")
    close = 500.0 + np.cumsum(rng.normal(0.5, 1.5, 120))
    close = np.maximum(close, 100.0)
    open_ = close + rng.normal(0, 0.3, 120)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.8, 120))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.8, 120))
    volume = rng.integers(50_000, 800_000, 120)
    df = pd.DataFrame({"Date": dates, "Open": open_, "High": high,
                       "Low": low, "Close": close, "Volume": volume})

    config = BacktestConfig(
        initial_cash=2_000_000,
        slippage_model=slippage,
        commission_model=commission,
        order_fee=10.0,
        tax_rate=0.0005,
        stamp_duty=0.0001,
        trades_per_year=252,
    )
    engine = BacktestEngine(config=config, strategy=_demo_strategy("DEMO", qty))
    return engine.run({"DEMO": df})


def _render(result: BacktestResult) -> None:
    """Render fills, orders, and cost breakdown."""
    divider()

    orders = result.orders
    fills = result.fills

    # Order lifecycle table
    if orders:
        section_header(f"📋 Order Lifecycle ({len(orders)})")
        rows = [o.to_dict() for o in orders]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if fills:
        section_header(f"💧 Fills ({len(fills)})")
        frows = [f.to_dict() for f in fills]
        st.dataframe(pd.DataFrame(frows), use_container_width=True, hide_index=True)

        total_commission = sum(f.commission for f in fills)
        total_slippage = sum(f.slippage_cost for f in fills)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Commission", fmt_number(total_commission))
        with col2:
            st.metric("Total Slippage", fmt_number(total_slippage))
        with col3:
            st.metric("Fill Count", str(len(fills)))
    else:
        empty_state("🫙", "No fills", "The strategy produced no executions.")

    # Status distribution
    if orders:
        from src.backtesting.models import OrderStatus
        counts: dict[str, int] = {}
        for o in orders:
            counts[o.status.value] = counts.get(o.status.value, 0) + 1
        try:
            import plotly.graph_objects as go
            fig = go.Figure(go.Bar(
                x=list(counts.keys()), y=list(counts.values()),
                marker_color=theme.chart_colors,
            ))
            fig.update_layout(
                title="Order Status Distribution", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", font=dict(color=theme.text),
                height=300, margin=dict(l=40, r=20, t=50, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.write(counts)
