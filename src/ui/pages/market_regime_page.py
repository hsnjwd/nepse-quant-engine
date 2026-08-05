"""Market Regime page — regime detection using live NEPSE index history."""

from __future__ import annotations

from typing import Any

import streamlit as st
import pandas as pd

from src.data import DataService as _DataService
from src.ui.components import regime_badge, kpi_card, line_chart


def _svc() -> _DataService:
    return _DataService()
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


def render() -> None:
    """Render the Market Regime page with live NEPSE index regime detection."""
    section_header("Market Regime", "Detect and analyse the current market regime from live NEPSE index data")

    # ── Detect button ────────────────────────────────────────────
    col1, col2 = st.columns([3, 1])
    with col2:
        detect_btn = st.button("🔍 Detect Regime", use_container_width=True, type="primary")

    divider()

    # ── Perform regime detection ─────────────────────────────────
    if detect_btn or st.session_state.get("regime_result") is not None:
        if detect_btn:
            with st.spinner("Downloading live NEPSE index history and detecting regime..."):
                try:
                    df = _svc().get_nepse_index_history(days=500)
                    if df is not None and not df.empty:
                        from src.regime.detector import MarketRegimeDetector
                        detector = MarketRegimeDetector()
                        result = detector.detect(df)
                    else:
                        result = None
                    st.session_state["regime_result"] = result
                    st.session_state["regime_df"] = df
                except Exception as e:
                    st.error(f"Regime detection failed: {e}")
                    return

        result = st.session_state.get("regime_result")
        df = st.session_state.get("regime_df")

        if result is None:
            st.warning("Could not detect market regime. NEPSE index history may be unavailable.")
            return

        # ── Main regime card ─────────────────────────────────────
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            regime_badge(result.regime)
            st.markdown(
                f"<p style='text-align: center; color: {theme.text}; font-size: 1.5rem; "
                f"font-weight: 700; margin: 0.5rem 0;'>{result.confidence:.1f}% Confidence</p>",
                unsafe_allow_html=True,
            )

        # ── Reasons ──────────────────────────────────────────────
        if result.reasons:
            st.markdown("##### Reasons")
            for reason in result.reasons:
                st.markdown(f"- {reason}")

        divider()

        # ── Metrics ──────────────────────────────────────────────
        section_header("Regime Metrics")
        metrics = result.metrics
        if metrics:
            cols = st.columns(4)
            metric_keys = [
                ("adx", "ADX"),
                ("atr", "ATR"),
                ("atr_pct", "ATR %"),
                ("rsi", "RSI"),
                ("trend", "Trend"),
                ("drawdown", "Drawdown"),
                ("volume_ratio", "Vol Ratio"),
                ("volume_spike", "Vol Spike"),
                ("ma_slope", "MA Slope"),
                ("price_position", "Price Pos"),
                ("volatility_20d", "Vol 20d"),
                ("obv_slope", "OBV Slope"),
            ]
            for i, (key, label) in enumerate(metric_keys):
                val = metrics.get(key)
                if val is not None:
                    with cols[i % 4]:
                        kpi_card(label, f"{val:.4f}" if isinstance(val, float) else str(val))

        divider()

        # ── NEPSE index price chart ──────────────────────────────
        if df is not None and not df.empty:
            section_header("NEPSE Index History")
            close_col = "Close" if "Close" in df.columns else None
            if close_col is not None:
                close_data = df[close_col].dropna()
                if len(close_data) > 1:
                    line_chart(
                        x=list(range(len(close_data))),
                        y=close_data.tolist(),
                        title="NEPSE Index — Historical Close",
                        x_label="Trading Periods",
                        y_label="Index Value",
                        height=400,
                    )
        else:
            st.info("NEPSE index history unavailable for charting.")

        divider()

        # ── Full metrics table ───────────────────────────────────
        section_header("All Metrics (Raw)")
        if metrics:
            metrics_df = pd.DataFrame([metrics])
            st.dataframe(metrics_df, use_container_width=True, hide_index=True)

    else:
        st.info("👆 Click **Detect Regime** to analyse the current NEPSE market regime using live index data.")
