"""Backtest Studio — institutional backtesting page (Sprint 9, Part 9.12).

Runs the event-driven :class:`BacktestEngine` with configurable order
types, slippage/commission models, corporate actions, and benchmark
comparison, then renders equity curve, drawdown, trades, and reports.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.engine import BacktestEngine
from src.backtesting.models import BacktestConfig, Order, OrderSide, OrderType
from src.backtesting.reports import InstitutionalReport
from src.data import DataService
from src.ui.components import kpi_card
from src.ui.helpers import section_header, divider, fmt_pct, fmt_number, empty_state
from src.ui.theme import theme

ORDER_TYPES = ["MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"]
SLIPPAGE_MODELS = ["fixed", "percentage", "volume", "volatility", "spread", "random"]
COMMISSION_MODELS = ["flat", "percentage", "tiered", "broker"]


def _svc() -> DataService:
    """Return the DataService singleton."""
    return DataService()


def _load_symbols() -> list[str]:
    """Load available symbols from uploaded history / local cache."""
    try:
        svc = _svc()
        live = svc.get_live_market()
        symbols = sorted({q.symbol for q in live if getattr(q, "symbol", "")})
        if symbols:
            return symbols
    except Exception:
        pass
    # Fallback to common NEPSE symbols when the API is unavailable.
    return ["NABIL", "ADBL", "PCBL", "NRIC", "NTC", "CHCL", "CZBIL", "NIFRA", "SHL", "EBL"]


def _history(symbol: str, days: int) -> pd.DataFrame:
    """Fetch price history through DataService."""
    try:
        hist = _svc().get_history(symbol, days=days)
        if not hist.is_empty:
            return hist.df
    except Exception:
        pass
    return pd.DataFrame()


def _demo_history(symbol: str, days: int = 250, seed: int | None = None) -> pd.DataFrame:
    """Build a deterministic demo OHLCV series for offline use."""
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
        "Date": dates,
        "Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume,
    })


def _rule_strategy(
    symbol: str,
    otype: str,
    qty: int,
    limit_pct: float,
    stop_pct: float,
    trail_pct: float,
):
    """Create a reusable strategy closure for the BacktestEngine.

    Generates a single entry order on the first bar and, depending on
    the order type, optional take-profit / stop-loss exits.
    """
    entered = {"flag": False}

    def strategy(bar_index: int, bars: dict[str, Any], ctx: Any) -> list[Order]:
        from src.backtesting.orders import OrderManager
        manager: OrderManager = ctx["order_manager"]
        bar = bars.get(symbol)
        if bar is None:
            return []
        price = bar.close

        orders: list[Order] = []
        if not entered["flag"] and price > 0:
            entered["flag"] = True
            side = OrderSide.BUY
            if otype == OrderType.MARKET:
                orders.append(manager.market(symbol, side, qty, note="studio entry"))
            elif otype == OrderType.LIMIT:
                orders.append(manager.limit(symbol, side, qty, price * (1 + limit_pct / 100.0)))
            elif otype == OrderType.STOP:
                orders.append(manager.stop(symbol, side, qty, price * (1 + stop_pct / 100.0)))
            elif otype == OrderType.STOP_LIMIT:
                orders.append(manager.stop_limit(symbol, side, qty, price * (1 + stop_pct / 100.0), price * (1 + limit_pct / 100.0)))
            elif otype == OrderType.TRAILING_STOP:
                orders.append(manager.trailing_stop(symbol, side, qty, price * (1 - trail_pct / 100.0), pct=trail_pct / 100.0))

            # Attach exits for bracket-style management.
            tp = price * (1 + limit_pct / 100.0)
            sl = price * (1 - stop_pct / 100.0)
            if tp > 0 and sl > 0:
                exit_side = OrderSide.SELL
                orders.append(manager.limit(symbol, exit_side, qty, tp, note="studio take-profit"))
                orders.append(manager.stop(symbol, exit_side, qty, sl, note="studio stop-loss"))
        return orders

    return strategy


def render() -> None:
    """Render the Backtest Studio page."""
    section_header("🎛️ Backtest Studio", "Institutional event-driven backtesting engine")

    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Symbol", _load_symbols(), key="bts_symbol")
        days = st.slider("History (days)", 60, 1000, 250, step=10, key="bts_days")
    with col2:
        capital = st.number_input("Initial Capital", min_value=10_000.0, value=1_000_000.0, step=50_000.0, key="bts_capital")
        order_type = st.selectbox("Entry Order Type", ORDER_TYPES, key="bts_otype")

    col1, col2, col3 = st.columns(3)
    with col1:
        quantity = st.number_input("Quantity", min_value=1, value=100, step=10, key="bts_qty")
    with col2:
        slippage_model = st.selectbox("Slippage Model", SLIPPAGE_MODELS, key="bts_slip")
    with col3:
        commission_model = st.selectbox("Commission Model", COMMISSION_MODELS, key="bts_comm")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        limit_pct = st.slider("Target / Limit %", 0.5, 50.0, 10.0, step=0.5, key="bts_target")
    with col2:
        stop_pct = st.slider("Stop %", 0.5, 50.0, 5.0, step=0.5, key="bts_stop")
    with col3:
        trail_pct = st.slider("Trail %", 0.5, 20.0, 5.0, step=0.5, key="bts_trail")
    with col4:
        tax_rate = st.number_input("Tax %", min_value=0.0, value=0.0, step=0.05, key="bts_tax") / 100.0

    col1, col2 = st.columns([1, 3])
    with col1:
        run = st.button("▶️ Run Backtest", type="primary", use_container_width=True, key="bts_run")

    divider()

    if not run:
        st.info("Configure the engine and click **Run Backtest**.")
        return

    with st.spinner("Running event-driven backtest..."):
        df = _history(symbol, days)
        if df.empty:
            df = _demo_history(symbol, days)
            st.caption("⚠️ Live history unavailable — using demo data.")

        config = BacktestConfig(
            initial_cash=capital,
            slippage_model=slippage_model,
            commission_model=commission_model,
            lot_size=1,
            allow_short=True,
            max_leverage=1.0,
            benchmark_symbol="NEPSE",
            tax_rate=tax_rate,
            trades_per_year=252,
        )
        strategy = _rule_strategy(
            symbol, OrderType(order_type), quantity, limit_pct, stop_pct, trail_pct,
        )
        engine = BacktestEngine(config=config, strategy=strategy)
        result = engine.run({symbol: df})

    # ── Headline metrics ────────────────────────────────────────
    m = result.metrics
    cols = st.columns(6)
    with cols[0]:
        kpi_card("📈 Total Return", fmt_pct(m.total_return))
    with cols[1]:
        kpi_card("📊 Sharpe", fmt_number(m.sharpe, 3))
    with cols[2]:
        kpi_card("📉 Max DD", fmt_pct(m.max_drawdown / 100.0))
    with cols[3]:
        kpi_card("🏆 Win Rate", fmt_pct(m.win_rate / 100.0))
    with cols[4]:
        kpi_card("🔄 Trades", str(m.trade_count))
    with cols[5]:
        kpi_card("💰 Final Equity", fmt_number(result.equity_curve[-1] if result.equity_curve else 0.0))

    # ── Equity curve + drawdown ─────────────────────────────────
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(
            x=list(range(len(result.equity_curve))), y=result.equity_curve,
            mode="lines", name="Equity", line=dict(color=theme.success, width=2),
        ), row=1, col=1)
        if result.benchmark_curve:
            fig.add_trace(go.Scatter(
                x=list(range(len(result.benchmark_curve))),
                y=result.benchmark_curve, mode="lines", name="Benchmark",
                line=dict(color=theme.text_muted, width=1.5, dash="dot"),
            ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=list(range(len(m.drawdown_series))), y=m.drawdown_series,
            mode="lines", name="Drawdown", fill="tozeroy",
            line=dict(color=theme.danger, width=1.5),
        ), row=2, col=1)
        fig.update_layout(
            height=480, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=theme.text), margin=dict(l=40, r=20, t=40, b=30),
            title=dict(text=f"{symbol} — Equity & Drawdown", font=dict(color=theme.text)),
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.line_chart(result.equity_curve)

    # ── Trades table ────────────────────────────────────────────
    if result.trades:
        divider()
        section_header(f"📋 Trades ({len(result.trades)})")
        rows = [t.to_dict() for t in result.trades]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        empty_state("🫙", "No trades generated", "Try a different order type or price level.")

    # ── Reports ─────────────────────────────────────────────────
    divider()
    section_header("📥 Export Reports")
    report = InstitutionalReport(result, title=f"Backtest Studio — {symbol}")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("📄 HTML Report", report.to_html().encode("utf-8"),
                           file_name=f"studio_{symbol.lower()}.html", mime="text/html", key="bts_html")
    with c2:
        st.download_button("📊 Excel Report", report.to_excel(),
                           file_name=f"studio_{symbol.lower()}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="bts_xlsx")
    with c3:
        st.download_button("📦 JSON Report", report.to_json(),
                           file_name=f"studio_{symbol.lower()}.json", mime="application/json", key="bts_json")

    # Persist for cross-page use.
    st.session_state["backtest_studio_result"] = result
    st.session_state["backtest_studio_symbol"] = symbol
