"""Stock Analysis page — live deep dive into a single stock with charts and indicators."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.engine.analyzer import analyze_dataframe
from src.regime.detector import MarketRegimeDetector
from src.ui.components import (
    kpi_card,
    signal_badge,
    regime_badge,
    candlestick_chart,
    indicator_chart,
)
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    safe_get,
    safe_float,
    fmt_number,
)
from src.ui.theme import theme
from src.data import DataService as _DataService


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the Analyze Stock page with live NEPSE data."""
    section_header("Analyze Stock", "Deep technical analysis for any NEPSE symbol")

    service = _svc()

    # ── Symbol selection ─────────────────────────────────────────
    col1, col2 = st.columns([3, 1])
    with col1:
        symbol_input = st.text_input(
            "Search Symbol",
            value=st.session_state.get("current_symbol", ""),
            placeholder="e.g., NABIL, SCB, CZBIL",
            key="analyze_symbol_input",
        )
        symbol = symbol_input.upper().strip()

    with col2:
        analyze_btn = st.button("🔍 Analyze", use_container_width=True, type="primary")

    if not symbol:
        st.info("👆 Enter a NEPSE symbol and click **Analyze**.")
        return

    divider()

    # ── Perform analysis using DataService ───────────────────────
    # ``_force_analyze`` is a one-shot flag set by the Upload OHLCV page
    # after saving history, so the next Analyze visit re-runs immediately.
    # It is consumed (popped) here rather than kept, so a failed analysis
    # (result stays None) is NOT retried on every auto-refresh rerun.
    force_analyze = st.session_state.pop("_force_analyze", False)
    if (
        analyze_btn
        or force_analyze
        or "analysis_result" not in st.session_state
        or st.session_state.get("current_symbol") != symbol
    ):
        with st.spinner(f"Downloading live data and analyzing {symbol}..."):
            try:
                # Uploaded history (saved via the Upload OHLCV page) takes
                # priority over live/API history — mirrors the static UI.
                uploaded = service.get_uploaded_history(symbol)
                if uploaded is not None and not uploaded.is_empty:
                    hist = uploaded
                    st.session_state["analysis_source"] = "uploaded"
                else:
                    hist = service.get_history(symbol, days=500)
                    st.session_state["analysis_source"] = "live"
                df = hist.df if not hist.is_empty else None

                result = None
                if df is not None and not df.empty:
                    result = analyze_dataframe(df)
                    result["symbol"] = symbol.upper()
                    quote = service.get_stock(symbol)
                    if quote:
                        result["live_price"] = quote.ltp
                        result["price"] = quote.ltp

                regime_result = None
                if df is not None and not df.empty:
                    try:
                        detector = MarketRegimeDetector()
                        regime_result = detector.detect(df)
                    except Exception:
                        pass

                st.session_state["analysis_result"] = result
                st.session_state["regime_result"] = regime_result
                st.session_state["current_symbol"] = symbol
                st.session_state["analysis_df"] = df
            except Exception as e:
                st.error(f"Analysis failed: {e}")
                return

    result = st.session_state.get("analysis_result")
    regime_result = st.session_state.get("regime_result")
    df = st.session_state.get("analysis_df")

    if not result:
        st.warning(f"Could not analyze {symbol}. The symbol may not have enough price history data available.")
        return

    # ── Data source banner ───────────────────────────────────────
    if st.session_state.get("analysis_source") == "uploaded":
        st.info(
            f"📤 Using **uploaded price history** for {symbol} "
            "(saved from the Upload OHLCV page)."
        )

    # ── Signal & Regime ──────────────────────────────────────────
    col1, col2 = st.columns([1, 3])
    with col1:
        signal_badge(safe_get(result, "signal"))
    with col2:
        if regime_result:
            regime_badge(regime_result.regime)

    divider()

    # ── Summary metrics ──────────────────────────────────────────
    cols = st.columns(5)
    with cols[0]:
        price = safe_float(safe_get(result, "live_price", safe_get(result, "price", 0)))
        kpi_card("Price", fmt_rupees(price))
    with cols[1]:
        kpi_card("Score", fmt_number(safe_float(safe_get(result, "score", 0)), 1))
    with cols[2]:
        kpi_card("Confidence", fmt_pct(safe_float(safe_get(result, "confidence", 0)) / 100.0))
    with cols[3]:
        kpi_card("RSI", fmt_number(safe_float(safe_get(result, "rsi", 0)), 1))
    with cols[4]:
        kpi_card("ATR", fmt_number(safe_float(safe_get(result, "atr", 0)), 2))

    # ── Tabs ─────────────────────────────────────────────────────
    tabs = st.tabs(["📊 Overview", "📈 Indicators", "📉 Charts", "📐 Support/Resistance"])

    # ── Tab 1: Overview ──────────────────────────────────────────
    with tabs[0]:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Pattern")
            st.write(f"**{safe_get(result, 'pattern', '—')}** ({safe_get(result, 'pattern_type', '—')})")
            st.markdown(f"*Strength:* {safe_get(result, 'pattern_strength', '—')}")
            st.markdown(f"*Score:* {safe_get(result, 'pattern_score', '—')}")
        with col2:
            st.markdown("##### Volume")
            st.markdown(f"**Signal:** {safe_get(result, 'volume_signal', '—')}")
            st.markdown(f"**Relative Vol:** {fmt_number(safe_float(safe_get(result, 'relative_volume', 0)), 2)}x")
            st.markdown(f"**Score:** {safe_get(result, 'volume_score', '—')}")

        if regime_result:
            st.markdown("##### Regime Details")
            cols = st.columns(3)
            metrics = regime_result.metrics
            if metrics:
                for i, (k, v) in enumerate(list(metrics.items())[:9]):
                    with cols[i % 3]:
                        st.metric(k.replace("_", " ").title(), f"{v:.4f}" if isinstance(v, float) else str(v))

    # ── Tab 2: Indicators ────────────────────────────────────────
    with tabs[1]:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Momentum")
            st.metric("RSI (14)", fmt_number(safe_float(safe_get(result, "rsi", 0)), 1))
            st.metric("MACD", fmt_number(safe_float(safe_get(result, "macd", 0)), 2))
            st.metric("MACD Signal", fmt_number(safe_float(safe_get(result, "macd_signal", 0)), 2))
            st.metric("MACD Histogram", fmt_number(safe_float(safe_get(result, "macd_histogram", 0)), 2))
        with col2:
            st.markdown("##### Volatility")
            st.metric("ATR (14)", fmt_number(safe_float(safe_get(result, "atr", 0)), 2))
            st.metric("BB Width", fmt_number(safe_float(safe_get(result, "bb_width", 0)), 4))
            st.metric("BB Position", fmt_number(safe_float(safe_get(result, "bb_position", 0)), 2))
            st.metric("Volatility 20d", fmt_number(safe_float(safe_get(result, "volatility_20d", 0)), 4))

        st.markdown("##### Score Breakdown")
        breakdown = safe_get(result, "score_breakdown", {})
        if breakdown:
            for k, v in breakdown.items():
                st.metric(k.replace("_", " ").title(), fmt_number(float(v), 1))

    # ── Tab 3: Charts ────────────────────────────────────────────
    with tabs[2]:
        if df is not None and not df.empty and len(df) > 1:
            # Ensure OHLCV columns exist
            has_ohlcv = all(c in df.columns for c in ["Open", "High", "Low", "Close"])
            if has_ohlcv:
                candlestick_chart(df, title=f"{symbol} — Candlestick", height=450)

                col1, col2 = st.columns(2)
                with col1:
                    # Volume chart
                    if "Volume" in df.columns:
                        vol_data = df["Volume"].dropna()
                        if len(vol_data) > 1:
                            indicator_chart(
                                list(range(len(vol_data))),
                                vol_data.tolist(),
                                title="Volume",
                                color=theme.info,
                                height=200,
                            )
                    # RSI chart
                    rsi_cols = [c for c in df.columns if "RSI" in c.upper()]
                    if rsi_cols:
                        rsi_data = df[rsi_cols[0]].dropna()
                        if len(rsi_data) > 1:
                            indicator_chart(
                                list(range(len(rsi_data))),
                                rsi_data.tolist(),
                                title="RSI (14)",
                                color=theme.primary,
                                height=200,
                            )
                    # MACD chart
                    if "MACD" in df.columns:
                        macd_data = df["MACD"].dropna()
                        if len(macd_data) > 1:
                            indicator_chart(
                                list(range(len(macd_data))),
                                macd_data.tolist(),
                                title="MACD",
                                color=theme.info,
                                height=200,
                                add_zero_line=True,
                            )

                with col2:
                    # Bollinger Bands
                    if "BB_UPPER" in df.columns and "BB_LOWER" in df.columns:
                        bb_close = df["Close"] - df["BB_LOWER"]
                        bb_range = df["BB_UPPER"] - df["BB_LOWER"]
                        bb_pct = (bb_close / bb_range.replace(0, float("nan"))).dropna()
                        if len(bb_pct) > 1:
                            indicator_chart(
                                list(range(len(bb_pct))),
                                bb_pct.tolist(),
                                title="Bollinger Band %B",
                                color=theme.warning,
                                height=200,
                            )
                    # OBV
                    if "OBV" in df.columns:
                        obv_data = df["OBV"].dropna()
                        if len(obv_data) > 1:
                            indicator_chart(
                                list(range(len(obv_data))),
                                obv_data.tolist(),
                                title="OBV",
                                color=theme.success,
                                height=200,
                            )
                    # ATR
                    if "ATR" in df.columns:
                        atr_data = df["ATR"].dropna()
                        if len(atr_data) > 1:
                            indicator_chart(
                                list(range(len(atr_data))),
                                atr_data.tolist(),
                                title="ATR (14)",
                                color=theme.danger,
                                height=200,
                            )
            else:
                st.info("Insufficient OHLCV columns for charting.")
        else:
            st.info("Price history not available for charting.")

    # ── Tab 4: Support / Resistance ──────────────────────────────
    with tabs[3]:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Support Levels")
            support = safe_get(result, "support", [])
            if isinstance(support, list):
                for level in support:
                    if isinstance(level, dict):
                        st.markdown(f"- **{level.get('level', '?')}:** {fmt_rupees(safe_float(level.get('price', 0)))}")
                    else:
                        st.markdown(f"- {fmt_rupees(safe_float(level))}")
            elif safe_float(support) > 0:
                st.markdown(f"- {fmt_rupees(safe_float(support))}")
            else:
                st.caption("No support levels detected")
        with col2:
            st.markdown("##### Resistance Levels")
            resistance = safe_get(result, "resistance", [])
            if isinstance(resistance, list):
                for level in resistance:
                    if isinstance(level, dict):
                        st.markdown(f"- **{level.get('level', '?')}:** {fmt_rupees(safe_float(level.get('price', 0)))}")
                    else:
                        st.markdown(f"- {fmt_rupees(safe_float(level))}")
            elif safe_float(resistance) > 0:
                st.markdown(f"- {fmt_rupees(safe_float(resistance))}")
            else:
                st.caption("No resistance levels detected")

        st.markdown("##### Trend")
        st.write(safe_get(result, "trend", "—"))
