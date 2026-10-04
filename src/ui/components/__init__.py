"""Reusable UI components for the NEPSE Quant Engine.

Each component is a self-contained function that renders a visual
element using Streamlit primitives and Plotly charts.

Figure functions use ``functools.lru_cache`` with a string key,
so identical inputs reuse the ``go.Figure`` object across reruns
without recreating it.
"""

from __future__ import annotations

import functools
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from src.ui.theme import theme
from src.ui.helpers import regime_emoji, signal_emoji, fmt_rupees, fmt_pct


# ═══════════════════════════════════════════════════════════════════
# Badges
# ═══════════════════════════════════════════════════════════════════


def signal_badge(signal: str | None) -> None:
    """Render a coloured signal badge (Strong Buy / Buy / Hold / Sell / Strong Sell)."""
    if not signal:
        signal = "HOLD"
    s = signal.upper().replace(" ", "_")
    color_map = {
        "STRONG_BUY": theme.signal_strong_buy,
        "BUY": theme.signal_buy,
        "HOLD": theme.signal_hold,
        "SELL": theme.signal_sell,
        "STRONG_SELL": theme.signal_strong_sell,
    }
    bg = color_map.get(s, theme.text_muted)
    emoji = signal_emoji(s)
    label = s.replace("_", " ").title()
    st.markdown(
        f"""
        <span style="
            background: {bg}22;
            color: {bg};
            border: 1px solid {bg}44;
            border-radius: 20px;
            padding: 4px 14px;
            font-size: 0.85rem;
            font-weight: 600;
            white-space: nowrap;
        ">{emoji} {label}</span>
        """,
        unsafe_allow_html=True,
    )


def regime_badge(regime: str | None) -> None:
    """Render a coloured market regime badge."""
    if not regime:
        regime = "UNKNOWN"
    r = regime.upper()
    color = theme.regime_color(r)
    emoji = regime_emoji(r)
    st.markdown(
        f"""
        <span style="
            background: {color}22;
            color: {color};
            border: 1px solid {color}44;
            border-radius: 20px;
            padding: 4px 14px;
            font-size: 0.85rem;
            font-weight: 600;
            white-space: nowrap;
        ">{emoji} {r}</span>
        """,
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════
# Metric KPI Cards
# ═══════════════════════════════════════════════════════════════════


def kpi_card(
    label: str,
    value: str,
    delta: str | None = None,
    help_text: str | None = None,
    use_container_width: bool = True,
) -> None:
    """Render a KPI metric card."""
    st.metric(
        label=label,
        value=value,
        delta=delta,
        help=help_text,
    )


# ═══════════════════════════════════════════════════════════════════
# Figure cache helpers
# ═══════════════════════════════════════════════════════════════════


def _cache_key(*args: Any, **kwargs: Any) -> str:
    """Produce a deterministic string key for figure memoization.

    Uses ``id()`` for objects with ``.columns`` (DataFrames) to avoid
    deep-copying on every call.  All other values are stringified.
    Note: the actual heavy data (OHLCV tuples) are passed separately
    to the cached builder; the cache key only needs the DataFrame id
    to distinguish instances.
    """
    parts: list[str] = []
    for a in args:
        parts.append(str(id(a)) if hasattr(a, "columns") else str(a))
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={str(id(v)) if hasattr(v, 'columns') else str(v)}")
    return "|".join(parts)


def clear_figure_caches() -> None:
    """Clear all LRU figure caches (call when theme changes)."""
    for fn in (_build_pie, _build_line, _build_candlestick, _build_indicator):
        fn.cache_clear()


# ═══════════════════════════════════════════════════════════════════
# Charts — public API functions
# ═══════════════════════════════════════════════════════════════════


def pie_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
    height: int = 400,
) -> None:
    """Render a Plotly pie chart (identical inputs reuse the figure)."""
    k = _cache_key(labels, values, title, height)
    fig = _build_pie(k, tuple(labels), tuple(values), title, height)
    st.plotly_chart(fig, use_container_width=True)


def line_chart(
    x: list[Any],
    y: list[float] | list[list[float]],
    names: str | list[str] = "Series",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    height: int = 400,
) -> None:
    """Render a Plotly line chart with one or more traces."""
    y_tuples = tuple(
        tuple(s) for s in y
    ) if (y and isinstance(y[0], list)) else tuple(y)
    k = _cache_key(x, y_tuples, str(names), title, x_label, y_label, height)
    fig = _build_line(k, tuple(x), y_tuples, str(names), title, x_label, y_label, height)
    st.plotly_chart(fig, use_container_width=True)


def candlestick_chart(
    df: Any,
    title: str = "",
    height: int = 500,
) -> None:
    """Render a Plotly candlestick chart (cached by DataFrame id)."""
    # Extract columns to tuples BEFORE passing to lru_cache (DataFrames are
    # not hashable).  The cache key uses id(df) so the same DataFrame object
    # instance reuses the figure across reruns.
    x = tuple(df.index) if hasattr(df, "index") else tuple(range(len(df)))
    o = tuple(df["Open"]) if "Open" in df.columns else ()
    h = tuple(df["High"]) if "High" in df.columns else ()
    lo = tuple(df["Low"]) if "Low" in df.columns else ()
    c = tuple(df["Close"]) if "Close" in df.columns else ()
    k = _cache_key(id(df), title, height)
    fig = _build_candlestick(k, x, o, h, lo, c, title, height)
    st.plotly_chart(fig, use_container_width=True)


def indicator_chart(
    x: list[Any],
    y: list[float],
    title: str = "",
    color: str | None = None,
    height: int = 200,
    add_zero_line: bool = False,
) -> None:
    """Render a small indicator chart (RSI, MACD, volume, etc.)."""
    k = _cache_key(x, y, title, color, height, add_zero_line)
    fig = _build_indicator(k, tuple(x), tuple(y), title, color or "", height, add_zero_line)
    st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════
# Cached figure builders (LRU, keyed by first string arg)
# ═══════════════════════════════════════════════════════════════════


@functools.lru_cache(maxsize=32)
def _build_pie(
    _key: str,
    labels: tuple[str, ...],
    values: tuple[float, ...],
    title: str = "",
    height: int = 400,
) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Pie(
                labels=list(labels),
                values=list(values),
                hole=0.4,
                marker=dict(
                    colors=theme.chart_colors[: len(labels)],
                    line=dict(color=theme.background, width=2),
                ),
                textinfo="label+percent",
                textfont=dict(color=theme.text, size=12),
            )
        ]
    )
    fig.update_layout(
        title=dict(text=title, font=dict(color=theme.text), x=0.5),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=True,
        legend=dict(font=dict(color=theme.text)),
    )
    return fig


@functools.lru_cache(maxsize=32)
def _build_line(
    _key: str,
    x: tuple,
    y_tuples: tuple,
    names_str: str,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    height: int = 400,
) -> go.Figure:
    fig = go.Figure()
    if y_tuples and isinstance(y_tuples[0], tuple):
        for i, series in enumerate(y_tuples):
            fig.add_trace(
                go.Scatter(
                    x=list(x),
                    y=list(series),
                    mode="lines",
                    name=f"Series {i + 1}",
                    line=dict(color=theme.chart_colors[i % len(theme.chart_colors)], width=2),
                )
            )
    else:
        fig.add_trace(
            go.Scatter(
                x=list(x),
                y=list(y_tuples),
                mode="lines",
                name="Series",
                line=dict(color=theme.chart_colors[0], width=2),
            )
        )
    fig.update_layout(
        title=dict(text=title, font=dict(color=theme.text), x=0.5),
        xaxis=dict(title=x_label, color=theme.text_secondary, gridcolor=theme.border),
        yaxis=dict(title=y_label, color=theme.text_secondary, gridcolor=theme.border),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=40, r=20, t=40, b=40),
        hovermode="x unified",
        legend=dict(font=dict(color=theme.text)),
    )
    return fig


@functools.lru_cache(maxsize=16)
def _build_candlestick(
    _key: str,
    x: tuple,
    open_: tuple,
    high: tuple,
    low: tuple,
    close: tuple,
    title: str = "",
    height: int = 500,
) -> go.Figure:
    """Build a Plotly candlestick figure (LRU-cached).

    All OHLCV data is passed as tuples so the cache key is hashable.
    """
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=list(x),
                open=list(open_),
                high=list(high),
                low=list(low),
                close=list(close),
                increasing_line_color=theme.success,
                decreasing_line_color=theme.danger,
            )
        ]
    )
    fig.update_layout(
        title=dict(text=title, font=dict(color=theme.text), x=0.5),
        xaxis=dict(color=theme.text_secondary, gridcolor=theme.border, rangeslider=dict(visible=False)),
        yaxis=dict(color=theme.text_secondary, gridcolor=theme.border),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=40, r=20, t=40, b=40),
        hovermode="x unified",
    )
    return fig


@functools.lru_cache(maxsize=64)
def _build_indicator(
    _key: str,
    x: tuple,
    y: tuple,
    title: str = "",
    color: str = "",
    height: int = 200,
    add_zero_line: bool = False,
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(x),
            y=list(y),
            mode="lines",
            fill="tozeroy",
            line=dict(color=color or theme.primary, width=1.5),
            fillcolor=f"{color or theme.primary}33",
        )
    )
    if add_zero_line:
        fig.add_hline(y=0, line=dict(color=theme.border, width=1, dash="dash"))
    fig.update_layout(
        title=dict(text=title, font=dict(color=theme.text, size=12), x=0.5),
        xaxis=dict(showgrid=False, color=theme.text_secondary),
        yaxis=dict(showgrid=True, gridcolor=theme.border, color=theme.text_secondary),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=20, r=20, t=30, b=20),
        showlegend=False,
    )
    return fig


# ═══════════════════════════════════════════════════════════════════
# Profile / Allocation bar
# ═══════════════════════════════════════════════════════════════════


def progress_bar(value: float, max_value: float = 100.0, color: str = theme.primary) -> None:
    """Render a custom progress bar."""
    pct = min(value / max_value * 100, 100) if max_value > 0 else 0
    st.markdown(
        f"""
        <div style="
            width: 100%;
            background: {theme.card_bg};
            border-radius: 4px;
            height: 8px;
            overflow: hidden;
        ">
            <div style="
                width: {pct:.1f}%;
                background: {color};
                height: 100%;
                border-radius: 4px;
                transition: width 0.3s ease;
            "></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
