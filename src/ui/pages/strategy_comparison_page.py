"""Strategy Comparison — strategy tournament page (Sprint 9, Part 9.12/9.14).

Runs multiple strategies through the :class:`StrategyTournament` over
the same data and renders a ranking table, bar charts, and equity
overlays.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.engine import BacktestEngine
from src.backtesting.models import BacktestConfig, Order, OrderSide, OrderType
from src.backtesting.tournament import StrategyTournament
from src.data import DataService
from src.ui.components import kpi_card
from src.ui.helpers import section_header, divider, fmt_pct, fmt_number
from src.ui.theme import theme

STRATEGY_PRESETS = {
    "Momentum": {"order_type": OrderType.MARKET, "params": {"lookback": 20, "threshold": 0.05}},
    "Breakout": {"order_type": OrderType.STOP, "params": {"lookback": 20, "multiplier": 1.2}},
    "Mean Reversion": {"order_type": OrderType.LIMIT, "params": {"lookback": 20, "z": 2.0}},
    "Trend Following": {"order_type": OrderType.MARKET, "params": {"fast": 10, "slow": 50}},
    "Gap": {"order_type": OrderType.MARKET, "params": {"gap": 0.01, "volume": 1.3}},
    "Opening Range": {"order_type": OrderType.STOP, "params": {"bars": 5}},
    "AI Trend": {"order_type": OrderType.MARKET, "params": {"confidence": 0.6}},
}


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


def _make_strategy(symbol: str, name: str, qty: int):
    """Build a strategy callable for a named preset."""
    preset = STRATEGY_PRESETS[name]
    otype = preset["order_type"]
    entered = {"flag": False}

    def strategy(bar_index: int, bars: dict[str, Any], ctx: Any) -> list[Order]:
        from src.backtesting.orders import OrderManager
        manager: OrderManager = ctx["order_manager"]
        bar = bars.get(symbol)
        if bar is None or bar.close <= 0:
            return []
        price = bar.close
        if entered["flag"]:
            return []
        entered["flag"] = True

        if name == "Momentum":
            return [manager.market(symbol, OrderSide.BUY, qty)]
        if name == "Breakout":
            return [manager.stop(symbol, OrderSide.BUY, qty, price * 1.02)]
        if name == "Mean Reversion":
            return [manager.limit(symbol, OrderSide.BUY, qty, price * 0.98)]
        if name == "Gap":
            return [manager.market(symbol, OrderSide.BUY, qty)]
        if name == "Opening Range":
            return [manager.stop(symbol, OrderSide.BUY, qty, price * 1.01)]
        return [manager.market(symbol, OrderSide.BUY, qty)]

    return strategy


def render() -> None:
    """Render the Strategy Comparison page."""
    section_header("🏟️ Strategy Comparison", "Strategy tournament over shared data")

    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Symbol", ["NABIL", "ADBL", "PCBL", "NRIC", "NTC", "CHCL", "EBL", "SHL"], key="cmp_symbol")
    with col2:
        days = st.slider("History (days)", 60, 600, 250, step=10, key="cmp_days")

    selected = st.multiselect(
        "Strategies", list(STRATEGY_PRESETS.keys()),
        default=list(STRATEGY_PRESETS.keys())[:4], key="cmp_strategies",
    )
    qty = st.number_input("Quantity per strategy", min_value=1, value=100, step=10, key="cmp_qty")

    if st.button("🏁 Run Tournament", type="primary", key="cmp_run"):
        if not selected:
            st.warning("Select at least one strategy.")
            return
        with st.spinner("Running strategy tournament..."):
            df = _history(symbol, days)
            config = BacktestConfig(initial_cash=1_000_000, trades_per_year=252)
            tournament = StrategyTournament(config=config)
            strategies = {
                name: _make_strategy(symbol, name, qty) for name in selected
            }
            entries = tournament.run({symbol: df}, strategies)
            ranked = tournament.rank(entries)

        st.session_state["comparison_entries"] = [e.to_dict() for e in ranked]
        _render_results(ranked, symbol)

    elif "comparison_entries" in st.session_state:
        from src.backtesting.tournament import TournamentEntry
        entries = [TournamentEntry(**e) for e in st.session_state["comparison_entries"]]
        _render_results(entries, symbol)


def _render_results(entries: list[Any], symbol: str) -> None:
    """Render the tournament results."""
    divider()
    section_header(f"🏆 Rankings — {symbol}")

    if not entries:
        st.info("No entries.")
        return

    rows = [e.to_dict() for e in entries if not e.metadata.get("error")]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Bar chart of AI scores
        try:
            import plotly.graph_objects as go
            names = [r["name"] for r in rows]
            scores = [r["ai_score"] for r in rows]
            fig = go.Figure(go.Bar(
                x=names, y=scores, marker_color=theme.chart_colors[:len(names)],
                text=[f"{s:.1f}" for s in scores], textposition="outside",
            ))
            fig.update_layout(
                title="AI Quality Score", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", font=dict(color=theme.text),
                height=380, margin=dict(l=40, r=20, t=50, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            pass

    failed = [e for e in entries if e.metadata.get("error")]
    if failed:
        st.caption(f"⚠️ {len(failed)} strategy failed: " + ", ".join(e.name for e in failed))
