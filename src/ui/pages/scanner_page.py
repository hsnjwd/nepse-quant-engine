"""Scanner page — professional market-wide stock scanning with saved filters, presets, and multi-column sorting."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st
import pandas as pd

from src.ui.components import kpi_card, signal_badge
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    safe_get,
    safe_float,
    fmt_number,
)
from src.ui.state import should_refresh, touch_cache
from src.data import DataService as _DataService
from src.ui.notifications import notification_manager as _notif


def _svc() -> _DataService:
    return _DataService()


_PRESETS_KEY = "_scanner_presets"
_PINNED_KEY = "_scanner_pinned"
_FAV_SCANS_KEY = "_scanner_fav_scans"


def render() -> None:
    """Render the professional Scanner page."""
    section_header("Market Scanner", "Scan, filter, sort, and rank all NEPSE stocks")

    # Initialise session state
    if _PRESETS_KEY not in st.session_state:
        st.session_state[_PRESETS_KEY] = {}
    if _PINNED_KEY not in st.session_state:
        st.session_state[_PINNED_KEY] = []
    if _FAV_SCANS_KEY not in st.session_state:
        st.session_state[_FAV_SCANS_KEY] = []

    # ── Scan controls ────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
    with col1:
        run_scan = st.button("🔍 Run Scan", use_container_width=True, type="primary")
    with col2:
        export_csv = st.button("📥 Export CSV", use_container_width=True)
    with col3:
        _render_preset_controls()
    with col4:
        st.caption(f"Auto: {st.session_state.get('auto_refresh', 'Manual')}")

    # ── Saved filter presets ─────────────────────────────────────
    if st.session_state[_PRESETS_KEY]:
        preset_names = list(st.session_state[_PRESETS_KEY].keys())
        selected_preset = st.selectbox("Load Preset", ["None"] + preset_names, key="scanner_preset_select")
        if selected_preset != "None":
            preset = st.session_state[_PRESETS_KEY][selected_preset]
            for k, v in preset.items():
                st.session_state[k] = v

    # ── Filter section ───────────────────────────────────────────
    divider()
    section_header("Filters")
    col1, col2, col3 = st.columns(3)

    with col1:
        signal_filter = st.selectbox(
            "Signal",
            ["All", "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"],
            key="scanner_signal",
        )
        regime_options = ["All"]
        cached = st.session_state.get("scanner_cache")
        if cached:
            all_r = safe_get(cached, "results", [])
            found = sorted(set(
                str(safe_get(r, "trend", "") or safe_get(r, "regime", "") or "—")
                for r in all_r if safe_get(r, "trend", "") or safe_get(r, "regime", "")
            ))
            if found:
                regime_options = ["All"] + found
        regime_filter = st.selectbox("Regime", regime_options, key="scanner_regime")

    with col2:
        min_score = st.slider("Min Score", 0, 100, 0, key="scanner_min_score")
        max_score = st.slider("Max Score", 0, 100, 100, key="scanner_max_score")
        min_confidence = st.slider("Min Confidence", 0, 100, 0, key="scanner_min_conf")

    with col3:
        rsi_min = st.slider("RSI Range", 0, 100, (0, 100), key="scanner_rsi_range")
        # ATR filter placeholder (not all scan results have ATR)
        text_filter = st.text_input("Symbol Search", placeholder="e.g. NABIL", key="scanner_text")
        price_range = st.slider("Price Range (₹)", 0.0, 10000.0, (0.0, 10000.0),
                                step=100.0, key="scanner_price_range")

    # Action: save current filter as preset
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        if st.button("💾 Save Preset", use_container_width=True):
            preset_name = st.text_input("Preset name", key="scanner_new_preset_name", label_visibility="collapsed")
            if preset_name:
                filters = {
                    "scanner_signal": signal_filter,
                    "scanner_regime": regime_filter,
                    "scanner_min_score": min_score,
                    "scanner_max_score": max_score,
                    "scanner_min_conf": min_confidence,
                }
                st.session_state[_PRESETS_KEY][preset_name] = filters
                _notif.notify_system(f"Saved filter preset: {preset_name}")
    with col2:
        if st.button("🔁 Import Filters", use_container_width=True):
            try:
                imported = st.text_area("Paste JSON filter preset", key="scanner_import")
                if imported:
                    data = json.loads(imported)
                    st.session_state[_PRESETS_KEY].update(data)
                    _notif.notify_system(f"Imported {len(data)} presets")
            except Exception as e:
                st.error(f"Import failed: {e}")
    with col3:
        if st.button("🔄 Reset Filters", use_container_width=True):
            keys_to_reset = [
                "scanner_signal", "scanner_regime", "scanner_min_score",
                "scanner_max_score", "scanner_min_conf", "scanner_rsi_range",
                "scanner_text", "scanner_price_range",
            ]
            for k in keys_to_reset:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()
    with col4:
        _render_favourite_toggle()

    divider()

    # ── Run scan ─────────────────────────────────────────────────
    results: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []

    if run_scan or st.session_state.get("scanner_cache") is not None:
        if run_scan or should_refresh(st.session_state.get("scanner_last_refresh"), 30):
            with st.spinner("Scanning all NEPSE stocks..."):
                try:
                    result = _svc().scan_market()
                    scan_data = {"results": result.results, "skipped": result.skipped}
                    st.session_state["scanner_cache"] = scan_data
                    touch_cache("scanner")
                    _notif.notify_scanner(f"Scan complete: {len(result.results)} stocks", priority="success")
                except Exception as e:
                    st.error(f"Scan failed: {e}")
                    scan_data = st.session_state.get("scanner_cache", {"results": [], "skipped": []})
        else:
            scan_data = st.session_state["scanner_cache"]

        raw_results = safe_get(scan_data, "results", [])
        skipped = safe_get(scan_data, "skipped", [])

        pinned = st.session_state.get(_PINNED_KEY, [])

        # Apply filters
        for r in raw_results:
            signal = safe_get(r, "signal", "HOLD").upper().replace("_", " ")
            score = safe_float(safe_get(r, "score", 0))
            confidence = safe_float(safe_get(r, "confidence", 0))
            trend = str(safe_get(r, "trend", "") or safe_get(r, "regime", "") or "")
            symbol = str(safe_get(r, "symbol", ""))
            price_val = safe_float(safe_get(r, "live_price", safe_get(r, "price", 0)))
            rsi_val = safe_float(safe_get(r, "rsi", 50))

            if signal_filter != "All" and signal.lower() != signal_filter.lower():
                continue
            if min_score > 0 and score < min_score:
                continue
            if max_score < 100 and score > max_score:
                continue
            if min_confidence > 0 and confidence < min_confidence:
                continue
            if regime_filter != "All" and trend != regime_filter:
                continue
            if text_filter and text_filter.upper() not in symbol.upper():
                continue
            if price_range[0] > 0 and price_val < price_range[0]:
                continue
            if price_range[1] < 10000 and price_val > price_range[1]:
                continue
            if rsi_min[0] > 0 and rsi_val < rsi_min[0]:
                continue
            if rsi_min[1] < 100 and rsi_val > rsi_min[1]:
                continue

            # Tag pinned symbols
            r["_pinned"] = symbol in pinned
            results.append(r)

        # Sort: pinned first, then by score descending
        results.sort(key=lambda x: (not x.get("_pinned", False), -safe_float(safe_get(x, "score", 0))))

        st.info(f"📊 Showing {len(results)} of {len(raw_results)} stocks matched"
                + (f" ({len(skipped)} skipped)" if skipped else ""))

    # ── Results table ────────────────────────────────────────────
    if results:
        rows = []
        for r in results:
            symbol = safe_get(r, "symbol", "—")
            is_pinned = symbol in st.session_state.get(_PINNED_KEY, [])
            rows.append({
                "📌": "📌" if is_pinned else "○",
                "Symbol": symbol,
                "Price": fmt_rupees(safe_float(safe_get(r, "live_price", safe_get(r, "price", 0)))),
                "Signal": safe_get(r, "signal", "HOLD"),
                "Score": fmt_number(safe_float(safe_get(r, "score", 0))),
                "Confidence": fmt_pct(safe_float(safe_get(r, "confidence", 0)) / 100.0),
                "RSI": fmt_number(safe_float(safe_get(r, "rsi", 0)), 1),
                "MACD": fmt_number(safe_float(safe_get(r, "macd", 0)), 2),
                "Regime": safe_get(r, "trend", "—"),
                "Support": fmt_rupees(safe_float(safe_get(r, "support", 0))),
                "Resistance": fmt_rupees(safe_float(safe_get(r, "resistance", 0))),
            })

        df = pd.DataFrame(rows)

        # Sortable configuration
        column_config = {
            "📌": st.column_config.TextColumn("📌", width="small"),
            "Symbol": st.column_config.TextColumn("Symbol", width="medium"),
            "Price": st.column_config.TextColumn("Price"),
            "Signal": st.column_config.TextColumn("Signal"),
            "Score": st.column_config.TextColumn("Score"),
            "Confidence": st.column_config.TextColumn("Confidence"),
            "RSI": st.column_config.TextColumn("RSI"),
            "MACD": st.column_config.TextColumn("MACD"),
            "Regime": st.column_config.TextColumn("Regime"),
            "Support": st.column_config.TextColumn("Support"),
            "Resistance": st.column_config.TextColumn("Resistance"),
        }

        st.dataframe(df, column_config=column_config, use_container_width=True, hide_index=True)

        # ── Action buttons ───────────────────────────────────────
        divider()
        section_header("Actions")
        selected = st.selectbox("Choose a symbol", [r.get("symbol", "?") for r in results if r.get("symbol")],
                                key="scanner_selected")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("📊 Analyze", use_container_width=True) and selected:
                st.session_state["current_symbol"] = selected
                st.session_state["page"] = "analyze"
                st.rerun()
        with col2:
            if st.button("🤖 AI Advisor", use_container_width=True) and selected:
                st.session_state["current_symbol"] = selected
                st.session_state["page"] = "ai_advisor"
                st.rerun()
        with col3:
            if st.button("📌 Toggle Pin", use_container_width=True) and selected:
                pinned = st.session_state.get(_PINNED_KEY, [])
                if selected in pinned:
                    pinned.remove(selected)
                else:
                    pinned.append(selected)
                st.session_state[_PINNED_KEY] = pinned
                st.rerun()
        with col4:
            if st.button("➕ Watchlist", use_container_width=True) and selected:
                try:
                    _svc().add_to_watchlist(selected.upper())
                    _notif.notify_system(f"Added {selected.upper()} to watchlist", priority="success")
                except Exception as e:
                    st.error(f"Could not add: {e}")

        # ── Export ───────────────────────────────────────────────
        if export_csv:
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", data=csv, file_name="nepse_scan_results.csv", mime="text/csv")
    elif not run_scan and st.session_state.get("scanner_cache") is None:
        st.info("👆 Click **Run Scan** to start scanning the entire NEPSE market.")


def _render_preset_controls() -> None:
    """Render preset management controls."""
    presets = st.session_state.get(_PRESETS_KEY, {})
    if presets:
        preset_to_delete = st.selectbox("Delete Preset", list(presets.keys()), key="scanner_del_preset")
        if st.button("🗑️ Delete Preset", use_container_width=True):
            if preset_to_delete in presets:
                del presets[preset_to_delete]
                st.session_state[_PRESETS_KEY] = presets
                _notif.notify_system(f"Deleted preset: {preset_to_delete}")
                st.rerun()


def _render_favourite_toggle() -> None:
    """Render favourite scans management."""
    if st.button("⭐ Save as Favourite", use_container_width=True):
        filters = {
            "scanner_signal": st.session_state.get("scanner_signal", "All"),
            "scanner_regime": st.session_state.get("scanner_regime", "All"),
            "scanner_min_score": st.session_state.get("scanner_min_score", 0),
        }
        st.session_state.setdefault(_FAV_SCANS_KEY, []).append(filters)
        _notif.notify_system("Saved as favourite scan")
