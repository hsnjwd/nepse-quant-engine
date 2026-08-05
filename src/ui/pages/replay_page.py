"""Market Replay page — replay historical sessions with live indicator updates.

Connects the :class:`MarketReplayEngine` to the UI: play/pause/step,
speed control, jump-to-date, and per-frame indicator + strategy decisions
computed from the replayed frame.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.ui.helpers import (
    section_header,
    divider,
    breadcrumb,
    fmt_rupees,
    fmt_pct,
    fmt_number,
    safe_get,
    safe_float,
)
from src.ui.components import kpi_card, signal_badge
from src.ui.notifications import notification_manager as _notif


def _svc() -> Any:
    from src.data import DataService

    return DataService()


def _engine() -> Any:
    """Return the session-scoped replay engine (created lazily)."""
    if "replay_engine" not in st.session_state:
        from src.replay.engine import MarketReplayEngine

        st.session_state["replay_engine"] = MarketReplayEngine()
    return st.session_state["replay_engine"]


def _reset_engine() -> None:
    """Stop and remove the session replay engine."""
    engine = st.session_state.pop("replay_engine", None)
    if engine is not None:
        try:
            engine.stop()
        except Exception:
            pass


def render() -> None:
    """Render the Market Replay page."""
    breadcrumb("Home", "Market", "Market Replay")
    section_header(
        "📼 Market Replay",
        "Replay historical NEPSE sessions with speed controls and live indicators",
    )

    engine = _engine()

    # ── Load controls ─────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        symbol = st.text_input("Symbol / Index", value="NEPSE", key="rp_symbol").upper().strip()
    with col2:
        days = st.selectbox("Days", [120, 250, 500, 1000], index=2, key="rp_days")
    with col3:
        use_index = st.checkbox("Use NEPSE index", value=True, key="rp_index")
    with col4:
        load = st.button("📥 Load", type="primary", use_container_width=True, key="rp_load")

    if load:
        with st.spinner(f"Loading {days} days of history for replay..."):
            target = "NEPSE" if use_index else symbol
            try:
                if use_index:
                    df = _svc().get_nepse_index_history(days=int(days))
                    ok = engine.load_dataframe(df, "NEPSE") if df is not None and not df.empty else False
                else:
                    hist = _svc().get_history(symbol, days=int(days))
                    ok = engine.load_dataframe(hist.df, symbol) if not hist.is_empty else False
                if not ok:
                    st.warning(f"No history available for {target}.")
                else:
                    st.session_state["rp_loaded"] = True
                    _notif.notify_market(f"Replay loaded: {target} — {engine.total_frames} bars")
            except Exception as exc:
                st.error(f"Could not load replay data: {exc}")

    if not st.session_state.get("rp_loaded") or engine.total_frames == 0:
        st.info("👆 Load a symbol or the NEPSE index to begin replaying.")
        return

    # ── Transport controls ─────────────────────────────────────────
    snapshot = engine.get_snapshot()
    progress = snapshot.progress_pct / 100.0 if snapshot.progress_pct else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1, 1, 1, 2])
    with c1:
        if st.button("⏮ Step Back", key="rp_back", use_container_width=True):
            engine.step_backward(1)
    with c2:
        if st.button("▶ Play", key="rp_play", use_container_width=True):
            engine.play(speed=engine.get_snapshot().speed)
    with c3:
        if st.button("⏸ Pause", key="rp_pause", use_container_width=True):
            engine.pause()
    with c4:
        if st.button("⏭ Step Fwd", key="rp_fwd", use_container_width=True):
            engine.step_forward(1)
    with c5:
        if st.button("⏹ Stop", key="rp_stop", use_container_width=True):
            engine.stop()
            st.session_state.pop("rp_loaded", None)
            _reset_engine()
            st.rerun()
    with c6:
        speed = st.slider("Speed", 1, 60, int(max(1, snapshot.speed)), key="rp_speed")
        if speed != int(snapshot.speed):
            engine.set_speed(float(speed))

    # Jump to date
    col1, col2 = st.columns([3, 1])
    with col1:
        jump_date = st.text_input("Jump to date (YYYY-MM-DD)", value="", key="rp_jump")
    with col2:
        if st.button("🎯 Jump", key="rp_jump_btn", use_container_width=True):
            if jump_date:
                frame = engine.go_to_date(jump_date)
                if frame is None:
                    st.warning(f"No frame near {jump_date}.")
                else:
                    st.success(f"Jumped to {jump_date}")

    st.progress(min(progress, 1.0))
    st.caption(
        f"Frame {snapshot.current_frame + 1}/{snapshot.total_frames} · "
        f"{snapshot.current_date or '—'} · {snapshot.speed:.0f}x · "
        f"state: {snapshot.state.value}"
    )

    divider()

    # ── Current frame data ─────────────────────────────────────────
    frame = engine.current_data
    if frame.empty:
        st.info("No frame data yet — press Step Fwd or Play.")
        return

    latest = frame.iloc[-1]
    close = safe_float(latest.get("Close", 0))
    open_ = safe_float(latest.get("Open", close))
    high = safe_float(latest.get("High", close))
    low = safe_float(latest.get("Low", close))
    volume = safe_float(latest.get("Volume", 0))

    cols = st.columns(5)
    with cols[0]:
        kpi_card("Close", fmt_rupees(close))
    with cols[1]:
        kpi_card("Open", fmt_rupees(open_))
    with cols[2]:
        kpi_card("High", fmt_rupees(high))
    with cols[3]:
        kpi_card("Low", fmt_rupees(low))
    with cols[4]:
        kpi_card("Volume", fmt_number(volume, 0))

    # ── Live indicator updates + strategy decision (Part 6) ──────
    decision = _analyze_frame(frame, symbol if not st.session_state.get("rp_index") else "NEPSE")
    if decision:
        c1, c2, c3 = st.columns(3)
        with c1:
            signal_badge(decision.get("signal", "HOLD"))
        with c2:
            kpi_card("Confidence", fmt_pct(safe_float(decision.get("confidence", 0)) / 100.0))
        with c3:
            kpi_card("Score", fmt_number(safe_float(decision.get("score", 0))))

        indicator_rows = [
            {"Indicator": "RSI", "Value": fmt_number(safe_float(decision.get("rsi", 0)), 1)},
            {"Indicator": "MACD", "Value": fmt_number(safe_float(decision.get("macd", 0)), 2)},
            {"Indicator": "ATR", "Value": fmt_number(safe_float(decision.get("atr", 0)), 2)},
            {"Indicator": "Support", "Value": fmt_rupees(safe_float(decision.get("support", 0)))},
            {"Indicator": "Resistance", "Value": fmt_rupees(safe_float(decision.get("resistance", 0)))},
        ]
        st.dataframe(pd.DataFrame(indicator_rows), use_container_width=True, hide_index=True)

    # ── Candlestick of replayed frame ─────────────────────────────
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        if all(c in frame.columns for c in ("Open", "High", "Low", "Close")):
            x = frame.get("Date", frame.index) if "Date" in frame.columns else frame.index
            fig.add_trace(
                go.Candlestick(
                    x=x,
                    open=frame["Open"],
                    high=frame["High"],
                    low=frame["Low"],
                    close=frame["Close"],
                    increasing_line_color="#00C853",
                    decreasing_line_color="#FF5252",
                    name=symbol,
                )
            )
        elif "Close" in frame.columns:
            fig.add_trace(go.Scatter(x=frame.index, y=frame["Close"], mode="lines", name="Close"))
        fig.update_layout(
            title=dict(text=f"{symbol} — Replay {snapshot.current_frame + 1}/{snapshot.total_frames}", x=0.5),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FFFFFF"),
            height=450,
            xaxis=dict(rangeslider=dict(visible=False)),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.info(f"Chart unavailable: {exc}")

    # ── Cache injection toggle ────────────────────────────────────
    divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💉 Inject Frame into Cache", key="rp_inject", use_container_width=True):
            try:
                engine.inject_into_cache()
                st.success("Current frame injected — dashboard/regime pages now show replayed data.")
            except Exception as exc:
                st.error(f"Injection failed: {exc}")
    with col2:
        if st.button("🔌 Restore Live Mode", key="rp_restore", use_container_width=True):
            try:
                engine.clear_cache_injection()
                st.success("Cache cleared — live mode restored.")
            except Exception as exc:
                st.error(f"Restore failed: {exc}")


def _analyze_frame(frame: pd.DataFrame, symbol: str) -> dict[str, Any] | None:
    """Run the analyzer + signal engine on the current replay frame.

    Returns a compact analysis dict, or ``None`` when analysis is
    unavailable (e.g. too few bars yet).
    """
    if frame is None or frame.empty or len(frame) < 10:
        return None
    try:
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(frame.copy())
        if not isinstance(result, dict):
            return None
        return {
            "signal": safe_get(result, "signal", "HOLD"),
            "confidence": safe_float(safe_get(result, "confidence", 0)),
            "score": safe_float(safe_get(result, "score", 0)),
            "rsi": safe_float(safe_get(result, "rsi", 0)),
            "macd": safe_float(safe_get(result, "macd", 0)),
            "atr": safe_float(safe_get(result, "atr", 0)),
            "support": safe_float(safe_get(result, "support", 0)),
            "resistance": safe_float(safe_get(result, "resistance", 0)),
        }
    except Exception:
        return None
