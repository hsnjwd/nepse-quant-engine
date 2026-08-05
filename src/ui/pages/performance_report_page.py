"""Performance Report — advanced analytics & institutional report page (Sprint 9, Part 9.12/9.11)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.models import BacktestConfig, Order, OrderSide
from src.backtesting.reports import InstitutionalReport
from src.ui.helpers import section_header, divider, fmt_number, fmt_pct, empty_state
from src.ui.theme import theme

METRIC_LABELS = [
    ("total_return", "Total Return", "pct"),
    ("annualized_return", "Annualized Return", "pct"),
    ("volatility", "Volatility", "pct"),
    ("sharpe", "Sharpe", "num"),
    ("sortino", "Sortino", "num"),
    ("calmar", "Calmar", "num"),
    ("max_drawdown", "Max Drawdown", "pct"),
    ("recovery_factor", "Recovery Factor", "num"),
    ("mar_ratio", "MAR Ratio", "num"),
    ("ulcer_index", "Ulcer Index", "num"),
    ("omega", "Omega", "num"),
    ("gain_loss_ratio", "Gain/Loss Ratio", "num"),
    ("sqn", "SQN", "num"),
    ("expectancy", "Expectancy", "num"),
    ("alpha", "Alpha", "num"),
    ("beta", "Beta", "num"),
    ("trade_count", "Trades", "int"),
    ("win_rate", "Win Rate", "pct"),
    ("profit_factor", "Profit Factor", "num"),
    ("exposure_time", "Exposure Time", "pct"),
]


def _run(symbol: str, qty: int) -> BacktestResult:
    """Run a deterministic demo backtest for the report."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(5)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=250, freq="B")
    close = 350.0 + np.cumsum(rng.normal(0.5, 1.8, 250))
    close = np.maximum(close, 50.0)
    open_ = close + rng.normal(0, 0.4, 250)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1.0, 250))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1.0, 250))
    volume = rng.integers(30_000, 600_000, 250)
    df = pd.DataFrame({"Date": dates, "Open": open_, "High": high,
                       "Low": low, "Close": close, "Volume": volume})

    def strategy(bar_index: int, bars: dict[str, Any], ctx: Any) -> list[Order]:
        from src.backtesting.orders import OrderManager
        manager: OrderManager = ctx["order_manager"]
        bar = bars.get(symbol)
        if bar is None or bar.close <= 0:
            return []
        price = bar.close
        if bar_index == 5:
            return [manager.market(symbol, OrderSide.BUY, qty)]
        if bar_index == 200:
            return [manager.limit(symbol, OrderSide.SELL, qty, price * 1.05)]
        return []

    config = BacktestConfig(initial_cash=2_000_000, trades_per_year=252)
    engine = BacktestEngine(config=config, strategy=strategy)
    return engine.run({symbol: df})


def render() -> None:
    """Render the Performance Report page."""
    section_header("📊 Performance Report", "Advanced analytics and institutional reporting")

    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Symbol", ["NABIL", "ADBL", "PCBL", "NRIC", "NTC", "CHCL", "EBL", "SHL"], key="pr_symbol")
    with col2:
        qty = st.number_input("Quantity", min_value=1, value=1000, step=100, key="pr_qty")

    if st.button("▶️ Generate Report", type="primary", key="pr_run"):
        with st.spinner("Generating..."):
            result = _run(symbol, qty)
        st.session_state["pr_result"] = result
        _render(result, symbol)

    elif "pr_result" in st.session_state:
        _render(st.session_state["pr_result"], symbol)


def _render(result: BacktestResult, symbol: str) -> None:
    """Render the metrics grid and report exports."""
    divider()
    m = result.metrics
    d = m.to_dict()

    # Metrics grid
    section_header("📐 Advanced Metrics")
    cols = st.columns(4)
    for idx, (key, label, kind) in enumerate(METRIC_LABELS):
        value = d.get(key, 0.0)
        if value is None:
            display = "N/A"
        elif kind == "pct":
            display = fmt_pct(value)
        elif kind == "int":
            display = str(int(value))
        else:
            display = fmt_number(value, 3)
        with cols[idx % 4]:
            st.metric(label, display)

    # Rolling metrics chart
    try:
        import plotly.graph_objects as go
        n = len(m.rolling_sharpe)
        x = list(range(n))
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=m.rolling_sharpe, mode="lines", name="Rolling Sharpe",
                                 line=dict(color=theme.chart_colors[0])))
        fig.add_trace(go.Scatter(x=x, y=m.rolling_sortino, mode="lines", name="Rolling Sortino",
                                 line=dict(color=theme.chart_colors[1])))
        fig.add_trace(go.Scatter(x=x, y=m.rolling_drawdown, mode="lines", name="Rolling DD",
                                 line=dict(color=theme.danger)))
        fig.update_layout(
            title="Rolling Risk Metrics", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", font=dict(color=theme.text),
            height=360, margin=dict(l=40, r=20, t=50, b=40), hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        pass

    # Equity curve
    if result.equity_curve:
        divider()
        section_header("📈 Equity Curve")
        st.line_chart(result.equity_curve)

    # Export center
    divider()
    section_header("📥 Institutional Reports")
    report = InstitutionalReport(result, title=f"Performance Report — {symbol}")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button("📄 HTML", report.to_html().encode("utf-8"),
                           file_name=f"perf_{symbol.lower()}.html", mime="text/html", key="pr_html")
    with c2:
        st.download_button("📊 Excel", report.to_excel(),
                           file_name=f"perf_{symbol.lower()}.xlsx", key="pr_xlsx")
    with c3:
        st.download_button("📦 JSON", report.to_json(),
                           file_name=f"perf_{symbol.lower()}.json", mime="application/json", key="pr_json")
    with c4:
        pdf = report.to_pdf()
        st.download_button("📕 PDF", pdf, file_name=f"perf_{symbol.lower()}.pdf",
                           mime="application/pdf", key="pr_pdf")
