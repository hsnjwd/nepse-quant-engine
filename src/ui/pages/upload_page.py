"""Upload OHLCV page — import individual stock price history from CSV.

Uploaded histories are persisted through :class:`src.data.DataService` and
take priority over live/API history in the Analyze Stock page, mirroring the
client-side ``Upload OHLCV`` tab of the static web UI.

The CSV must contain ``Date, Open, High, Low, Close, Volume`` columns.
Common header aliases (``LTP``, ``No of Shares Traded``, etc.) are accepted
and normalised before saving.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.data import DataService as _DataService
from src.ui.helpers import section_header, divider, fmt_rupees, fmt_number
from src.ui.theme import theme

# Lazy import Plotly — the page works without it.
try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# Common NEPSE CSV header aliases -> canonical column names
_HEADER_ALIASES: dict[str, str] = {
    "date": "Date",
    "datetime": "Date",
    "timestamp": "Date",
    "day": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "closing": "Close",
    "adj close": "Close",
    "last traded price": "Close",
    "ltp": "Close",
    "volume": "Volume",
    "vol": "Volume",
    "no of shares traded": "Volume",
    "no. of shares traded": "Volume",
    "no. of shares": "Volume",
}

REQUIRED_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")

_SAMPLE_CSV = """Date,Open,High,Low,Close,Volume
2025-01-02,482.50,489.90,480.10,487.25,512300
2025-01-05,487.00,492.50,485.20,490.15,634800
2025-01-06,490.30,495.80,488.10,493.60,701100
2025-01-07,493.00,499.00,491.40,496.75,588900
2025-01-08,496.50,502.10,494.80,500.30,845000
"""


def _svc() -> _DataService:
    """Get or create the DataService singleton."""
    return _DataService()


def _normalise_header(df: pd.DataFrame) -> pd.DataFrame:
    """Map common header aliases onto canonical OHLCV column names."""
    rename: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        target = _HEADER_ALIASES.get(key)
        if target:
            rename[col] = target
    return df.rename(columns=rename)


def parse_ohlcv_csv(data: bytes) -> pd.DataFrame:
    """Parse raw CSV bytes into a cleaned OHLCV DataFrame.

    Args:
        data: Raw bytes of the uploaded CSV file.

    Returns:
        A DataFrame with ``Date`` parsed as ``datetime``, numeric columns
        coerced, duplicate dates removed, and rows sorted by date.
    """
    df = pd.read_csv(io.BytesIO(data))
    df = _normalise_header(df)
    df.columns = [str(c).strip() for c in df.columns]

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"])
        df = df.drop_duplicates(subset=["Date"])
        df = df.sort_values("Date")

    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])

    df = df.reset_index(drop=True)
    return df


def render() -> None:
    """Render the Upload OHLCV page."""
    section_header(
        "Upload OHLCV",
        "Import CSV price history for individual stocks. Saved data is used by "
        "Analyze Stock before live/API history.",
    )

    service = _svc()

    # ── Upload control ──────────────────────────────────────────
    uploaded = st.file_uploader(
        "Choose a CSV file (Date, Open, High, Low, Close, Volume)",
        type=["csv", "txt"],
        key="ohlcv_uploader",
    )

    default_symbol = ""
    if uploaded is not None:
        default_symbol = Path(uploaded.name).stem.upper()

    symbol = st.text_input(
        "Symbol",
        value=st.session_state.get("upload_symbol", default_symbol),
        placeholder="e.g., NABIL",
        key="upload_symbol",
    ).strip().upper()

    if uploaded is None:
        st.info(
            "👆 Upload a CSV with columns **Date, Open, High, Low, Close, Volume**. "
            "Common aliases (LTP, No of Shares Traded) are auto-detected."
        )
        with st.expander("📄 Download a sample CSV"):
            st.code(_SAMPLE_CSV, language="csv")
            st.download_button(
                "⬇️ Download sample.csv",
                data=_SAMPLE_CSV,
                file_name="sample_ohlcv.csv",
                mime="text/csv",
            )
        _render_saved_symbols(service)
        return

    # ── Parse & validate ─────────────────────────────────────────
    try:
        df = parse_ohlcv_csv(uploaded.getvalue())
    except Exception as exc:
        st.error(f"Could not parse CSV: {exc}")
        _render_saved_symbols(service)
        return

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        st.error(
            f"CSV is missing required columns: {', '.join(missing)}. "
            f"Expected: {', '.join(REQUIRED_COLUMNS)}."
        )
        st.dataframe(df.head(10), use_container_width=True)
        _render_saved_symbols(service)
        return

    if df.empty:
        st.error("CSV contains no valid rows after parsing.")
        _render_saved_symbols(service)
        return

    # ── Preview ─────────────────────────────────────────────────
    st.markdown("##### Preview")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Rows", fmt_number(len(df), 0))
    with col2:
        st.metric("Start", str(df["Date"].iloc[0].date()))
    with col3:
        st.metric("End", str(df["Date"].iloc[-1].date()))
    with col4:
        st.metric("Latest Close", fmt_rupees(float(df["Close"].iloc[-1])))

    if HAS_PLOTLY and len(df) > 1:
        chart = df.tail(120)
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=chart["Date"],
                    open=chart["Open"],
                    high=chart["High"],
                    low=chart["Low"],
                    close=chart["Close"],
                    increasing_line_color=theme.success,
                    decreasing_line_color=theme.danger,
                )
            ]
        )
        fig.update_layout(
            title=f"{symbol or 'Uploaded'} — Price Preview",
            xaxis_title="Date",
            yaxis_title="Price",
            height=380,
            xaxis_rangeslider_visible=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=theme.text),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.line_chart(df.set_index("Date")["Close"])

    with st.expander("📋 Show first 25 rows"):
        st.dataframe(df.head(25), use_container_width=True, hide_index=True)

    # ── Save ─────────────────────────────────────────────────────
    divider()
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(
            f"**Symbol:** `{symbol or '—'}` — data will be saved through DataService "
            f"and used by **Analyze Stock** before any API call."
        )
    with col2:
        if not symbol:
            st.warning("Enter a symbol to save.")
        else:
            if st.button("💾 Save to DataService", type="primary", use_container_width=True):
                try:
                    history = service.save_history(symbol, df)
                    st.session_state["analysis_result"] = None
                    st.session_state["analysis_df"] = None
                    st.session_state["current_symbol"] = symbol
                    # One-shot flag consumed by the Analyze Stock page so the
                    # next visit re-runs analysis with the uploaded history.
                    st.session_state["_force_analyze"] = True
                    st.success(
                        f"Saved {len(history.df)} rows for **{symbol}**. "
                        f"Analyze Stock will now use this uploaded history first."
                    )
                except Exception as exc:
                    st.error(f"Could not save history: {exc}")

    _render_saved_symbols(service)


def _render_saved_symbols(service: _DataService) -> None:
    """Render the list of uploaded symbols with delete controls."""
    divider()
    st.markdown("##### 📚 Saved Uploaded Histories")

    symbols = service.list_uploaded_symbols()
    if not symbols:
        st.caption("No uploaded histories yet.")
        return

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        hist = service.get_uploaded_history(sym)
        if hist is None or hist.is_empty:
            continue
        rows.append(
            {
                "Symbol": sym,
                "Rows": len(hist.df),
                "Start": str(hist.df["Date"].iloc[0].date()),
                "End": str(hist.df["Date"].iloc[-1].date()),
            }
        )

    if not rows:
        st.caption("No uploaded histories yet.")
        return

    st.dataframe(rows, use_container_width=True, hide_index=True)

    col1, col2 = st.columns([1, 3])
    with col1:
        delete_symbol = st.selectbox("Delete symbol", [""] + [r["Symbol"] for r in rows])
    with col2:
        if delete_symbol and st.button("🗑️ Delete", use_container_width=True):
            if service.delete_uploaded_history(delete_symbol):
                st.success(f"Deleted uploaded history for **{delete_symbol}**.")
                st.rerun()
            else:
                st.warning(f"No uploaded history found for {delete_symbol}.")
