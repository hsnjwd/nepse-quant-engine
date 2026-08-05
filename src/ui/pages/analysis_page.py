"""Stock Analysis page — candlestick chart, indicators, support/resistance.

NOTE: This page is NOT registered in app.py and is kept for legacy reference.
The active analysis page is ``src/ui/pages/analyze_page.py``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
import pandas as pd

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
from src.logging.logger import logger


def render_analysis_page() -> None:
    """Render the stock analysis page."""
    section_header("📈 Stock Analysis", "Analyse individual stocks with charts and indicators.")

    symbol = st.text_input(
        "Ticker Symbol",
        placeholder="e.g. NABIL, CHCL, NLIC",
    ).strip().upper()

    if not symbol:
        st.info("👆 Enter a ticker symbol to begin analysis.")
        return

    col1, col2 = st.columns([1, 3])
    with col1:
        load_button = st.button("📥 Load Data", type="primary", use_container_width=True)
        st.checkbox("Use cached data", value=True, key="analysis_use_cached")

    if load_button:
        with st.spinner(f"Loading data for {symbol}..."):
            try:
                from src.engine.analyzer import analyze_stock
                from src.config import DATA_DIRECTORY
                from pathlib import Path

                data_path = Path(DATA_DIRECTORY) / f"{symbol.lower()}.csv"

                if not data_path.exists():
                    st.error(f"No data file found for '{symbol}'. Expected: {data_path}")
                    return

                analysis = analyze_stock(str(data_path))
                st.session_state["selected_symbol"] = symbol
                st.session_state["selected_stock_data"] = analysis

                # ── KPI Row ──
                cols = st.columns(6)
                kpis = [
                    ("Current Price", fmt_rupees(safe_float(analysis.get("price", 0.0)))),
                    ("Signal", str(analysis.get("signal", "HOLD"))),
                    ("Confidence", fmt_pct(safe_float(analysis.get("confidence", 0.0)) / 100)),
                    ("RSI", fmt_number(safe_float(analysis.get("rsi", 0.0)), 1)),
                    ("Trend", str(analysis.get("trend", "N/A"))),
                    ("Score", fmt_number(safe_float(analysis.get("score", 0.0)), 1)),
                ]
                for i, (label, value) in enumerate(kpis):
                    with cols[i]:
                        st.metric(label, value)

                divider()

                # ── Charts ──
                try:
                    from src.data import DataService
                    svc = DataService()
                    hist = svc.get_history(symbol, days=365)
                    df = hist.df if not hist.is_empty else pd.DataFrame()
                except Exception:
                    df = pd.DataFrame()

                if not df.empty and "Date" in df.columns:
                    df["Date"] = pd.to_datetime(df["Date"])
                    df = df.sort_values("Date")

                try:
                    # Candlestick
                    if all(c in df.columns for c in ["Date", "Open", "High", "Low", "Close"]) and not df.empty:
                        import plotly.graph_objects as go

                        fig_candle = go.Figure(
                            data=[
                                go.Candlestick(
                                    x=df["Date"].tail(100),
                                    open=df["Open"].tail(100),
                                    high=df["High"].tail(100),
                                    low=df["Low"].tail(100),
                                    close=df["Close"].tail(100),
                                    increasing_line_color=theme.success,
                                    decreasing_line_color=theme.danger,
                                )
                            ]
                        )
                        fig_candle.update_layout(
                            title=f"{symbol} — Price",
                            xaxis_title="Date",
                            yaxis_title="Price",
                            height=400,
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            font=dict(color=theme.text),
                        )
                        st.plotly_chart(fig_candle, use_container_width=True)

                    # Volume bars
                    if "Volume" in df.columns and not df.empty:
                        import plotly.graph_objects as go

                        fig_vol = go.Figure(
                            data=[go.Bar(x=df["Date"].tail(100), y=df["Volume"].tail(100),
                                          marker_color=theme.info)]
                        )
                        fig_vol.update_layout(
                            title=f"{symbol} — Volume",
                            xaxis_title="Date",
                            yaxis_title="Volume",
                            height=250,
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            font=dict(color=theme.text),
                        )
                        st.plotly_chart(fig_vol, use_container_width=True)

                except ImportError:
                    st.info("Plotly charts unavailable. Install with: pip install plotly")

                # ── Analysis Details ──
                divider()
                st.subheader("Analysis Details")
                details = []
                for key in ["rsi", "macd", "macd_signal", "sma20", "sma50", "support", "resistance"]:
                    if key in analysis:
                        details.append({"Indicator": key.upper(), "Value": fmt_number(safe_float(analysis[key]), 2)})
                if details:
                    st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)

            except Exception as exc:
                logger.exception("Analysis failed for %s: %s", symbol, exc)
                st.error(f"Analysis failed: {exc}")
    else:
        st.info(f"👆 Enter a symbol and click **Load Data** to analyse {symbol}.")

    logger.info("Analysis page rendered for symbol=%s.", symbol)
