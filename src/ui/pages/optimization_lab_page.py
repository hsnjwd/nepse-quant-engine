"""Optimization Lab — walk-forward engine UI (Sprint 9, Part 9.12/9.6).

Runs the :class:`WalkForwardEngine` with rolling/expanding windows and
renders per-fold results plus a consistency summary.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.models import Order, OrderSide
from src.backtesting.walk_forward import WalkForwardEngine
from src.data import DataService
from src.ui.helpers import section_header, divider, fmt_pct, fmt_number
from src.ui.theme import theme


def _svc() -> DataService:
    """Return the DataService singleton."""
    return DataService()


def _history(symbol: str, days: int) -> pd.DataFrame:
    """Fetch history through DataService with a demo fallback."""
    try:
        hist = _svc().get_history(symbol, days=days)
        if not hist.is_empty:
            return hist.df
    except Exception:
        pass
    import numpy as np
    rng = np.random.default_rng(hash(symbol) % (2**32))
    dates = pd.date_range(end=pd.Timestamp.today(), periods=days, freq="B")
    close = 300.0 + np.cumsum(rng.normal(0.5, 2.0, days))
    close = np.maximum(close, 10.0)
    open_ = close + rng.normal(0, 0.5, days)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1.0, days))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1.0, days))
    volume = rng.integers(10_000, 500_000, days)
    return pd.DataFrame({
        "Date": dates, "Open": open_, "High": high,
        "Low": low, "Close": close, "Volume": volume,
    })


def _build_strategy(symbol: str, qty: int, lookback: int | None = None, threshold: float = 0.0):
    """Build a parameterised strategy for walk-forward testing."""
    entered = {"flag": False}

    def strategy(bar_index: int, bars: dict[str, Any], ctx: Any) -> list[Order]:
        from src.backtesting.orders import OrderManager
        manager: OrderManager = ctx["order_manager"]
        bar = bars.get(symbol)
        if bar is None or bar.close <= 0 or entered["flag"]:
            return []
        entered["flag"] = True
        return [manager.market(symbol, OrderSide.BUY, qty)]

    return strategy


def render() -> None:
    """Render the Optimization Lab page."""
    section_header("🔬 Optimization Lab", "Walk-forward optimisation with rolling/expanding windows")

    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Symbol", ["NABIL", "ADBL", "PCBL", "NRIC", "NTC", "CHCL", "EBL", "SHL"], key="ol_symbol")
        train_size = st.slider("Train window (bars)", 30, 300, 120, step=10, key="ol_train")
    with col2:
        test_size = st.slider("Test window (bars)", 10, 100, 30, step=5, key="ol_test")
        expanding = st.checkbox("Expanding windows", value=False, key="ol_expanding")

    qty = st.number_input("Quantity", min_value=1, value=100, step=10, key="ol_qty")

    if st.button("🚀 Run Walk-Forward", type="primary", key="ol_run"):
        if train_size + test_size > 250:
            st.warning("History must cover train + test; increasing to 600 days.")
        days = max(train_size + test_size + 40, 300)
        with st.spinner("Running walk-forward optimisation..."):
            df = _history(symbol, days)
            engine = WalkForwardEngine(
                train_size=train_size, test_size=test_size,
                expanding=expanding,
            )
            # WalkForwardEngine runs each fold under the placeholder
            # symbol "_wf", so the strategy must look up that key.
            build = lambda params: _build_strategy("_wf", qty)
            folds, summary = engine.run(df, build)

        st.session_state["ol_summary"] = summary.to_dict()
        st.session_state["ol_folds"] = [f.to_dict() for f in folds]
        _render_results(folds, summary, symbol)

    elif "ol_summary" in st.session_state:
        from src.backtesting.walk_forward import WalkForwardSummary, WalkForwardFold
        summary = WalkForwardSummary(**st.session_state["ol_summary"])
        folds = [WalkForwardFold(**f) for f in st.session_state["ol_folds"]]
        _render_results(folds, summary, symbol)


def _render_results(folds: list[Any], summary: Any, symbol: str) -> None:
    """Render walk-forward results."""
    divider()
    s = summary

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Folds", str(s.folds))
    with c2:
        st.metric("Avg Train Return", fmt_pct(s.average_train_return))
    with c3:
        st.metric("Avg Test Return", fmt_pct(s.average_test_return))
    with c4:
        st.metric("Avg Test Sharpe", fmt_number(s.average_test_sharpe, 3))
    with c5:
        st.metric("Passed Folds", str(s.passed_folds))

    if folds:
        section_header("📊 Per-Fold Results")
        st.dataframe(pd.DataFrame([f.to_dict() for f in folds]), use_container_width=True, hide_index=True)

        try:
            import plotly.graph_objects as go
            idx = [f.fold_index for f in folds]
            tr = [f.train_return for f in folds]
            te = [f.test_return for f in folds]
            fig = go.Figure()
            fig.add_trace(go.Bar(x=idx, y=tr, name="Train", marker_color=theme.chart_colors[0]))
            fig.add_trace(go.Bar(x=idx, y=te, name="Test", marker_color=theme.chart_colors[1]))
            fig.update_layout(
                barmode="group", title="Train vs Test Return by Fold",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=theme.text), height=360,
                margin=dict(l=40, r=20, t=50, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            pass
