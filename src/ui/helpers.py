"""Helper utilities for the NEPSE Quant Engine Streamlit UI.

Provides formatting, display, and data-processing functions reused
across multiple pages.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.ui.theme import theme


# ── Number formatting ─────────────────────────────────────────────


def fmt_rupees(value: float | None, decimals: int = 2) -> str:
    """Format a number as NPR currency string.

    Args:
        value: The numeric value.
        decimals: Number of decimal places.

    Returns:
        Formatted string like ``"₹ 1,234.56"`` or ``"N/A"``.
    """
    if value is None:
        return "N/A"
    sign = "-" if value < 0 else ""
    return f"{sign}₹ {abs(value):,.{decimals}f}"


def fmt_pct(value: float | None, decimals: int = 2) -> str:
    """Format a number as a percentage string.

    Args:
        value: Decimal ratio (e.g. 0.1234).
        decimals: Number of decimal places.

    Returns:
        Formatted string like ``"+12.34%"`` or ``"N/A"``.
    """
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.{decimals}f}%"


def fmt_number(value: float | None, decimals: int = 2) -> str:
    """Format a number with commas.

    Args:
        value: The numeric value.
        decimals: Number of decimal places.

    Returns:
        Formatted string like ``"1,234.56"``.
    """
    if value is None:
        return "N/A"
    return f"{value:,.{decimals}f}"


def fmt_signal(signal: str | None) -> str:
    """Normalise a signal string for display."""
    if not signal:
        return "HOLD"
    return signal.upper().replace(" ", "_")


def regime_emoji(regime: str) -> str:
    """Return an emoji for a market regime."""
    emojis = {
        "BULL": "🐂",
        "BEAR": "🐻",
        "PANIC": "🚨",
        "OVERHEATED": "🔥",
        "RECOVERY": "🔄",
        "ACCUMULATION": "📥",
        "DISTRIBUTION": "📤",
        "SIDEWAYS": "➡️",
        "LOW_VOLATILITY": "😴",
        "HIGH_VOLATILITY": "⚡",
        "UNKNOWN": "❓",
    }
    return emojis.get(regime.upper(), "❓")


def signal_emoji(signal: str) -> str:
    """Return an emoji for a signal."""
    emojis = {
        "STRONG_BUY": "🚀",
        "BUY": "🟢",
        "HOLD": "🟡",
        "SELL": "🔴",
        "STRONG_SELL": "💥",
    }
    return emojis.get(signal.upper(), "⚪")


# ── Safe accessors ────────────────────────────────────────────────


def safe_get(data: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """Safely get a value from a dict, returning *default* if None."""
    if data is None:
        return default
    return data.get(key, default)


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert to float, returning *default* on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ── Layout helpers ────────────────────────────────────────────────


def spaced_columns(count: int) -> list[Any]:
    """Create equally spaced columns with small gaps."""
    return st.columns([1] + [0.1] * (count - 1) if count > 1 else [1])


def metric_card(
    label: str,
    value: str,
    delta: str | None = None,
    help_text: str | None = None,
) -> None:
    """Render a styled metric card using Streamlit's metric component."""
    st.metric(
        label=label,
        value=value,
        delta=delta,
        help=help_text,
    )


def section_header(title: str, subtitle: str | None = None) -> None:
    """Render a section header with optional subtitle."""
    st.markdown(
        f"""
        <div style="margin-bottom: 1rem;">
            <h3 style="color: {theme.text}; margin-bottom: 0.25rem;">{title}</h3>
            {f'<p style="color: {theme.text_secondary}; font-size: 0.9rem;">{subtitle}</p>' if subtitle else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


def divider() -> None:
    """Render a themed horizontal divider."""
    st.markdown(
        f"<hr style='border-color: {theme.border}; margin: 1.5rem 0;'/>",
        unsafe_allow_html=True,
    )


def breadcrumb(*crumbs: str) -> None:
    """Render a breadcrumb navigation trail (Part 9 — UX).

    Args:
        *crumbs: Breadcrumb labels, e.g. ``breadcrumb("Home", "Market", "Scanner")``.
    """
    trail = f" <span style='color: {theme.text_muted};'>/</span> ".join(
        f"<span style='color: {theme.text_secondary};'>{c}</span>" for c in crumbs
    )
    st.markdown(
        f"<div style='font-size: 0.8rem; margin-bottom: 0.25rem;'>{trail}</div>",
        unsafe_allow_html=True,
    )


def empty_state(
    icon: str,
    title: str,
    hint: str | None = None,
) -> None:
    """Render a friendly empty state (Part 9 — UX).

    Args:
        icon: Emoji or icon character.
        title: Short title text.
        hint: Optional helper text.
    """
    hint_html = (
        f"<p style='color: {theme.text_muted}; font-size: 0.85rem; "
        f"margin-top: 4px;'>{hint}</p>" if hint else ""
    )
    st.markdown(
        f"""
        <div style="
            background: {theme.card_bg};
            border: 1px dashed {theme.border};
            border-radius: {theme.border_radius};
            padding: 1.5rem;
            text-align: center;
            margin: 0.5rem 0;
        ">
            <div style="font-size: 2rem;">{icon}</div>
            <div style="color: {theme.text}; font-weight: 600; margin-top: 6px;">{title}</div>
            {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
