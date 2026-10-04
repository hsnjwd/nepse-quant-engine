"""Watchlist page — track tracked symbols with live signals and automatic refresh."""

from __future__ import annotations

from typing import Any

import streamlit as st
import pandas as pd

from src.data import DataService as _DataService
from src.ui.components import kpi_card


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
from src.ui.state import should_refresh, touch_cache


def render() -> None:
    """Render the Watchlist page with live data refresh."""
    section_header("Watchlist", "Track your favourite symbols with live signals")

    # ── Actions ──────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns([1, 1, 2, 2])
    with col1:
        refresh = st.button("🔄 Refresh", use_container_width=True, type="primary")
    with col2:
        scan_watchlist = st.button("🔍 Scan Now", use_container_width=True)
    with col3:
        add_symbol = st.text_input("Add Symbol", placeholder="e.g., NABIL", label_visibility="collapsed")
    with col4:
        if add_symbol:
            if st.button("➕ Add", use_container_width=True, key="add_wl_btn"):
                try:
                    _svc().add_to_watchlist(add_symbol.upper().strip())
                    st.success(f"Added {add_symbol.upper()}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not add: {e}")

    divider()

    # ── Load watchlist ───────────────────────────────────────────
    try:
        watchlist = _svc().get_watchlist()
    except Exception:
        watchlist = []

    if not watchlist:
        st.info("👆 Your watchlist is empty. Add symbols above or from the Scanner page.")
        return

    # ── Auto-refresh every 30s ───────────────────────────────────
    if should_refresh(st.session_state.get("watchlist_last_refresh"), 30) or refresh or scan_watchlist:
        touch_cache("watchlist")
        st.session_state["watchlist_scanning"] = True
    else:
        st.session_state["watchlist_scanning"] = False

    # ── Analyze each symbol with live data ───────────────────────
    results: list[dict[str, Any]] = []
    should_scan = st.session_state.get("watchlist_scanning", True)

    if should_scan:
        progress = st.progress(0, text="Scanning watchlist with live data...")
        for i, symbol in enumerate(watchlist):
            progress.progress((i + 1) / len(watchlist), text=f"Analyzing {symbol}...")
            try:
                svc = _svc()
                # Try full analysis first
                hist = svc.get_history(symbol, days=365)
                if not hist.is_empty:
                    from src.engine.analyzer import analyze_dataframe
                    result = analyze_dataframe(hist.df)
                    result["symbol"] = symbol
                    quote = svc.get_stock(symbol)
                    if quote:
                        result["live_price"] = quote.ltp
                        result["price"] = quote.ltp
                    results.append(result)
                else:
                    # Fallback: just live price
                    quote = svc.get_stock(symbol)
                    if quote:
                        results.append({
                            "symbol": symbol,
                            "price": quote.ltp,
                            "live_price": quote.ltp,
                            "signal": "HOLD",
                            "score": 0,
                            "confidence": 0,
                            "rsi": 0,
                            "date": "",
                        })
                    else:
                        results.append({"symbol": symbol, "error": "No live data"})
            except Exception as e:
                results.append({"symbol": symbol, "error": str(e)})
        progress.empty()
        st.session_state["watchlist_data"] = results
    else:
        results = st.session_state.get("watchlist_data", [])

    # ── Summary ──────────────────────────────────────────────────
    buy_count = sum(1 for r in results if safe_get(r, "signal", "").upper() in ("BUY", "STRONG_BUY"))
    sell_count = sum(1 for r in results if safe_get(r, "signal", "").upper() in ("SELL", "STRONG_SELL"))
    error_count = sum(1 for r in results if "error" in r)

    cols = st.columns(3)
    with cols[0]:
        kpi_card("📊 Total", str(len(results)))
    with cols[1]:
        kpi_card("🟢 Buy Signals", str(buy_count))
    with cols[2]:
        kpi_card("🔴 Sell Signals", str(sell_count))

    divider()

    # ── Table ────────────────────────────────────────────────────
    rows = []
    for r in results:
        if "error" in r:
            rows.append({
                "Symbol": r.get("symbol", "—"),
                "Price": "—",
                "Signal": "ERROR",
                "Score": "—",
                "Confidence": "—",
                "RSI": "—",
                "Last Update": "—",
            })
        else:
            price = safe_float(safe_get(r, "live_price", safe_get(r, "price", 0)))
            rows.append({
                "Symbol": safe_get(r, "symbol", "—"),
                "Price": fmt_rupees(price) if price > 0 else "—",
                "Signal": safe_get(r, "signal", "HOLD"),
                "Score": fmt_number(safe_float(safe_get(r, "score", 0)), 1),
                "Confidence": fmt_pct(safe_float(safe_get(r, "confidence", 0)) / 100.0),
                "RSI": fmt_number(safe_float(safe_get(r, "rsi", 0)), 1),
                "Last Update": "Live" if price > 0 else "—",
            })

    df = pd.DataFrame(rows)

    def color_signal(val: str) -> str:
        color_map = {
            "STRONG BUY": "background: #00C85333; color: #00E676",
            "BUY": "background: #00C85322; color: #00C853",
            "HOLD": "background: #FFC10722; color: #FFC107",
            "SELL": "background: #FF525222; color: #FF5252",
            "STRONG SELL": "background: #FF174422; color: #FF1744",
            "ERROR": "background: #FF525211; color: #FF5252",
        }
        return color_map.get(val, "")

    st.dataframe(
        df.style.applymap(color_signal, subset=["Signal"]),
        use_container_width=True,
        hide_index=True,
    )

    divider()

    # ── Remove symbol ────────────────────────────────────────────
    selected_remove = st.selectbox("Remove symbol", [""] + [s for s in watchlist if s])
    if selected_remove and st.button("🗑️ Remove", use_container_width=True):
        try:
            _svc().remove_from_watchlist(selected_remove)
            st.success(f"Removed {selected_remove}")
            st.rerun()
        except Exception as e:
            st.error(f"Could not remove: {e}")
