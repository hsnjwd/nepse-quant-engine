"""Scenario Lab — stress-testing page (Sprint 9, Part 9.12/9.9).

Applies market scenarios (bull, bear, sideways, flash crash,
liquidity crisis, volatility regimes) to data and runs the backtest
engine through each, rendering a comparison table.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.engine import BacktestEngine
from src.backtesting.models import BacktestConfig, Order, OrderSide
from src.backtesting.scenarios import ScenarioLab, SCENARIOS
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


def _strategy(symbol: str, qty: int):
    """Simple buy-and-hold-then-exit strategy for scenario runs."""
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
    """Render the Scenario Lab page."""
    section_header("🧪 Scenario Lab", "Stress-test strategies across market scenarios")

    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Symbol", ["NABIL", "ADBL", "PCBL", "NRIC", "NTC", "CHCL", "EBL", "SHL"], key="sl_symbol")
    with col2:
        days = st.slider("History (days)", 60, 500, 250, step=10, key="sl_days")

    scenarios = st.multiselect("Scenarios", list(SCENARIOS), default=list(SCENARIOS), key="sl_scenarios")
    seed = st.number_input("Seed", min_value=0, value=42, step=1, key="sl_seed")
    qty = st.number_input("Quantity", min_value=1, value=100, step=10, key="sl_qty")

    if st.button("🧪 Run Scenario Lab", type="primary", key="sl_run"):
        with st.spinner("Running scenario stress tests..."):
            df = _history(symbol, days)
            config = BacktestConfig(initial_cash=1_000_000, trades_per_year=252)
            lab = ScenarioLab(
                engine_factory=lambda: BacktestEngine(config=config, strategy=_strategy(symbol, qty))
            )
            results = lab.run_all({symbol: df}, seed=seed, scenarios=scenarios)

        st.session_state["sl_results"] = [r.to_dict() for r in results]
        _render_results(results)

    elif "sl_results" in st.session_state:
        from src.backtesting.scenarios import ScenarioResult
        results = [ScenarioResult(**r) for r in st.session_state["sl_results"]]
        _render_results(results)


def _render_results(results: list[Any]) -> None:
    """Render the scenario comparison table and chart."""
    divider()
    section_header("📊 Scenario Comparison")

    if not results:
        st.info("No results.")
        return

    rows = [r.to_dict() for r in results]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    try:
        import plotly.graph_objects as go
        names = [r.scenario for r in results]
        rets = [r.total_return for r in results]
        dds = [r.max_drawdown for r in results]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=names, y=rets, name="Return", marker_color=theme.chart_colors[0]))
        fig.add_trace(go.Bar(x=names, y=dds, name="Max DD %", marker_color=theme.chart_colors[3]))
        fig.update_layout(
            barmode="group", title="Scenario Stress Test",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=theme.text), height=380,
            margin=dict(l=40, r=20, t=50, b=60),
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        pass

    for r in results:
        st.caption(f"**{r.scenario}**: return {r.total_return*100:.2f}%, max DD {r.max_drawdown:.2f}%, "
                   f"Sharpe {r.sharpe:.2f}, trades {r.trades}")
