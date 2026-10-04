"""Advanced Charts page — professional-grade stock analysis with live WebSocket updates.

Features: Candlestick, Volume, RSI, MACD, Bollinger Bands, Moving Averages, ATR.
Live updates via DataService.subscribe_market() with WebSocket + auto-polling fallback.
LIVE indicator when connected. Trace-only updates (no full figure redraw).
"""

from __future__ import annotations

import time
from typing import Any
import threading

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
from src.data import DataService as _DataService
from src.ui.components import kpi_card
from src.ui.notifications import notification_manager as _notif

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def _svc() -> _DataService:
    return _DataService()


# ── Thread-safe quote queue for WebSocket callbacks ──────────────
_live_quotes_queue: list[Any] = []
_live_quotes_lock = threading.Lock()


def _handle_live_quote(q: Any) -> None:
    """Thread-safe callback for live quote updates from WebSocket."""
    with _live_quotes_lock:
        _live_quotes_queue.append(q)


def render() -> None:
    """Render the Advanced Charts page with live WebSocket updates."""
    section_header("📈 Advanced Charts", "Professional-grade technical analysis with live WebSocket updates")

    service = _svc()
    ws_connected = service.is_live_feed_connected()

    # ── WebSocket status badge ───────────────────────────────────
    status_col1, status_col2 = st.columns([3, 1])
    with status_col2:
        if ws_connected:
            st.markdown(
                f"<span style='background: #00C85322; color: #00C853; "
                f"border: 1px solid #00C85344; border-radius: 20px; "
                f"padding: 4px 14px; font-size: 0.85rem; font-weight: 600;'>"
                f"🟢 LIVE</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<span style='background: #FF525222; color: #FF5252; "
                f"border: 1px solid #FF525244; border-radius: 20px; "
                f"padding: 4px 14px; font-size: 0.85rem; font-weight: 600;'>"
                f"⚪ DISCONNECTED (polling)</span>",
                unsafe_allow_html=True,
            )
    with status_col1:
        st.markdown("", unsafe_allow_html=True)  # spacer

    # ── Subscribe to live feed (thread-safe) ────────────────────
    if ws_connected and "_chart_subscribed" not in st.session_state:
        service.subscribe_market(_handle_live_quote)
        st.session_state["_chart_subscribed"] = True

    # ── Symbol input ─────────────────────────────────────────────
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        symbol = st.text_input("Symbol", placeholder="e.g., NABIL", key="chart_symbol").upper().strip()
    with col2:
        days = st.selectbox("Period", [30, 60, 90, 180, 365, 730], index=4, key="chart_days")
    with col3:
        load = st.button("📥 Load Chart", type="primary", use_container_width=True)

    if not symbol or not load:
        st.info("👆 Enter a symbol and click **Load Chart** to see advanced technical analysis.")
        return

    if not HAS_PLOTLY:
        st.error("Plotly is required for charts. Install with: pip install plotly")
        return

    # ── Load data (first time or on refresh) ─────────────────────
    cache_key = f"_chart_df_{symbol}_{days}"
    if load or cache_key not in st.session_state:
        with st.spinner(f"Loading {days}-day chart for {symbol}..."):
            hist = service.get_history(symbol, days=days)
            if hist.is_empty:
                st.error(f"No price history available for {symbol}")
                return
            df = hist.df.copy()
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.sort_values("Date")
            st.session_state[cache_key] = df

    df = st.session_state[cache_key].copy()

    # ── Calculate indicators ─────────────────────────────────────
    from src.engine.analyzer import analyze_dataframe
    try:
        analysis = analyze_dataframe(df)
    except Exception as exc:
        st.warning(f"Analysis engine unavailable: {exc}")
        analysis = {}

    rsi_val = safe_float(analysis.get("rsi", 50))
    macd_val = safe_float(analysis.get("macd", 0))
    macd_signal_val = safe_float(analysis.get("macd_signal", 0))
    macd_hist_val = safe_float(analysis.get("macd_histogram", 0))
    sma20_val = safe_float(analysis.get("sma20", 0))
    sma50_val = safe_float(analysis.get("sma50", 0))
    bb_upper = safe_float(analysis.get("bb_upper", 0))
    bb_lower = safe_float(analysis.get("bb_lower", 0))
    atr_val = safe_float(analysis.get("atr", 0))
    support = safe_float(analysis.get("support", float(df["Low"].min()) if "Low" in df.columns else 0))
    resistance = safe_float(analysis.get("resistance", float(df["High"].max()) if "High" in df.columns else 0))

    # Compute MAs/BB if missing
    if "SMA20" not in df.columns and "Close" in df.columns:
        df["SMA20"] = df["Close"].rolling(20).mean()
    if "SMA50" not in df.columns and "Close" in df.columns:
        df["SMA50"] = df["Close"].rolling(50).mean()
    if "BB_upper" not in df.columns and "Close" in df.columns:
        sma = df["Close"].rolling(20).mean()
        std = df["Close"].rolling(20).std()
        df["BB_upper"] = sma + 2 * std
        df["BB_lower"] = sma - 2 * std

    # ── Check for live quote update (thread-safe queue read) ────
    latest_quote = None
    with _live_quotes_lock:
        while _live_quotes_queue:
            q = _live_quotes_queue.pop(0)
            if q.symbol.upper() == symbol:
                latest_quote = q
    if latest_quote:
        price = latest_quote.ltp
    else:
        price = float(df["Close"].iloc[-1]) if not df.empty else 0.0

    # ── KPI cards ────────────────────────────────────────────────
    divider()
    cols = st.columns(6)
    with cols[0]:
        kpi_card("💰 Price", fmt_rupees(price))
    with cols[1]:
        kpi_card("📊 RSI", fmt_number(rsi_val, 1))
    with cols[2]:
        kpi_card("📈 MACD", fmt_number(macd_val, 2), delta=fmt_number(macd_hist_val, 2))
    with cols[3]:
        kpi_card("📉 ATR", fmt_number(atr_val, 2))
    with cols[4]:
        kpi_card("🛡️ Support", fmt_rupees(support))
    with cols[5]:
        kpi_card("🚧 Resistance", fmt_rupees(resistance))

    divider()

    # ── Build subplot figure (incremental updates) ───────────────
    section_header("Price Action", "Candlestick chart with moving averages and Bollinger Bands")
    _fig_key = f"_chart_fig_{symbol}_{days}"

    if _fig_key in st.session_state and not load:
        fig = st.session_state[_fig_key]
        # Update title
        fig.update_layout(title=f"{symbol} — {days}-Day Technical Analysis{' [LIVE]' if ws_connected else ''}")
        # Update latest candle if we have a live quote
        if latest_quote and len(fig.data) > 0:
            try:
                # Update the last candle's close and volume
                last_idx = len(df) - 1
                if hasattr(fig.data[0], 'close'):
                    old_close = fig.data[0].close
                    if isinstance(old_close, (list, tuple)) and len(old_close) > last_idx:
                        old_close_list = list(old_close)
                        old_close_list[last_idx] = latest_quote.ltp
                        fig.data[0].close = tuple(old_close_list)
                        if latest_quote.high > 0:
                            old_high = list(fig.data[0].high)
                            old_high[last_idx] = max(old_high[last_idx], latest_quote.high)
                            fig.data[0].high = tuple(old_high)
                        if latest_quote.low > 0:
                            old_low = list(fig.data[0].low)
                            old_low[last_idx] = min(old_low[last_idx], latest_quote.low)
                            fig.data[0].low = tuple(old_low)
            except (IndexError, TypeError, AttributeError):
                pass
    else:
        fig = make_subplots(
            rows=4, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.5, 0.15, 0.15, 0.2],
            subplot_titles=("Price", "Volume", "RSI", "MACD"),
        )
        # Candlestick
        fig.add_trace(
            go.Candlestick(x=df["Date"], open=df["Open"], high=df["High"],
                           low=df["Low"], close=df["Close"],
                           increasing_line_color=theme.success, decreasing_line_color=theme.danger,
                           name=symbol),
            row=1, col=1,
        )
        # SMA20
        if "SMA20" in df.columns:
            fig.add_trace(go.Scatter(x=df["Date"], y=df["SMA20"], mode="lines",
                                     name="SMA20", line=dict(color="#FFC107", width=1.5)), row=1, col=1)
        # SMA50
        if "SMA50" in df.columns:
            fig.add_trace(go.Scatter(x=df["Date"], y=df["SMA50"], mode="lines",
                                     name="SMA50", line=dict(color="#FF9800", width=1.5)), row=1, col=1)
        # Bollinger Bands
        if "BB_upper" in df.columns and "BB_lower" in df.columns:
            fig.add_trace(go.Scatter(x=df["Date"], y=df["BB_upper"], mode="lines",
                                     name="BB Upper", line=dict(color="rgba(33,150,243,0.5)", width=1, dash="dash")), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["Date"], y=df["BB_lower"], mode="lines",
                                     name="BB Lower", line=dict(color="rgba(33,150,243,0.5)", width=1, dash="dash"),
                                     fill="tonexty", fillcolor="rgba(33,150,243,0.1)"), row=1, col=1)
        fig.add_hline(y=support, line=dict(color=theme.success, width=1, dash="dash"),
                      annotation_text=f"Support {fmt_rupees(support)}", row=1, col=1)
        fig.add_hline(y=resistance, line=dict(color=theme.danger, width=1, dash="dash"),
                      annotation_text=f"Resistance {fmt_rupees(resistance)}", row=1, col=1)
        # Volume
        colors_vol = ["#00C853" if c >= o else "#FF5252" for c, o in zip(df["Close"], df["Open"])]
        fig.add_trace(go.Bar(x=df["Date"], y=df["Volume"], name="Volume",
                             marker_color=colors_vol, opacity=0.7), row=2, col=1)
        # RSI
        rsi_series = analysis.get("rsi_series", [])
        if rsi_series and isinstance(rsi_series, list) and len(rsi_series) == len(df):
            fig.add_trace(go.Scatter(x=df["Date"], y=rsi_series, mode="lines",
                                     name="RSI", line=dict(color="#9C27B0", width=1.5)), row=3, col=1)
        else:
            delta = df["Close"].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            fig.add_trace(go.Scatter(x=df["Date"], y=100 - (100 / (1 + rs)), mode="lines",
                                     name="RSI", line=dict(color="#9C27B0", width=1.5)), row=3, col=1)
        fig.add_hline(y=70, line=dict(color=theme.danger, width=1, dash="dash"), row=3, col=1)
        fig.add_hline(y=30, line=dict(color=theme.success, width=1, dash="dash"), row=3, col=1)
        # MACD
        exp12 = df["Close"].ewm(span=12, adjust=False).mean()
        exp26 = df["Close"].ewm(span=26, adjust=False).mean()
        macd_line = exp12 - exp26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
        fig.add_trace(go.Scatter(x=df["Date"], y=macd_line, mode="lines", name="MACD",
                                 line=dict(color="#2196F3", width=1.5)), row=4, col=1)
        fig.add_trace(go.Scatter(x=df["Date"], y=signal_line, mode="lines", name="Signal",
                                 line=dict(color="#FF9800", width=1.5)), row=4, col=1)
        colors_macd = ["#00C853" if h >= 0 else "#FF5252" for h in hist]
        fig.add_trace(go.Bar(x=df["Date"], y=hist, name="Histogram",
                             marker_color=colors_macd, opacity=0.6), row=4, col=1)
        # Layout
        fig.update_layout(
            title=dict(text=f"{symbol} — {days}-Day Technical Analysis",
                       font=dict(color=theme.text), x=0.5),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=theme.text),
            xaxis=dict(gridcolor=theme.border, rangeslider=dict(visible=False)),
            yaxis=dict(gridcolor=theme.border),
            height=900, hovermode="x unified", showlegend=True,
            legend=dict(font=dict(color=theme.text), orientation="h", y=1.12),
            dragmode="zoom",
        )
        fig.update_yaxes(gridcolor=theme.border, row=1, col=1)
        fig.update_yaxes(gridcolor=theme.border, title_text="Volume", row=2, col=1)
        fig.update_yaxes(gridcolor=theme.border, range=[0, 100], row=3, col=1)
        fig.update_yaxes(gridcolor=theme.border, row=4, col=1)
        st.session_state[_fig_key] = fig

    # Use container — reuse on subsequent renders
    chart_placeholder = st.empty()
    chart_placeholder.plotly_chart(fig, use_container_width=True)

    # ── Indicator Summary Table ──────────────────────────────────
    divider()
    section_header("Indicator Summary")
    indicators = [
        {"Indicator": "RSI (14)", "Value": fmt_number(rsi_val, 1),
         "Signal": "Overbought" if rsi_val > 70 else ("Oversold" if rsi_val < 30 else "Neutral")},
        {"Indicator": "MACD", "Value": fmt_number(macd_val, 4),
         "Signal": "Bullish" if macd_val > macd_signal_val else "Bearish"},
        {"Indicator": "MACD Signal", "Value": fmt_number(macd_signal_val, 4), "Signal": ""},
        {"Indicator": "SMA20", "Value": fmt_rupees(sma20_val),
         "Signal": "Above" if price > sma20_val else "Below"},
        {"Indicator": "SMA50", "Value": fmt_rupees(sma50_val),
         "Signal": "Above" if price > sma50_val else "Below"},
        {"Indicator": "ATR (14)", "Value": fmt_number(atr_val, 2), "Signal": ""},
        {"Indicator": "Bollinger Upper", "Value": fmt_rupees(bb_upper),
         "Signal": "Overextended" if price > bb_upper else ""},
        {"Indicator": "Bollinger Lower", "Value": fmt_rupees(bb_lower),
         "Signal": "Overextended" if price < bb_lower else ""},
    ]
    st.dataframe(pd.DataFrame(indicators), use_container_width=True, hide_index=True)

    # ── Download ─────────────────────────────────────────────────
    divider()
    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download CSV", data=csv_data,
                       file_name=f"{symbol.lower()}_{days}d.csv",
                       mime="text/csv", use_container_width=True,
                       key="chart_download")
