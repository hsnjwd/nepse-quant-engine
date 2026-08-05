"""Theme configuration for the NEPSE Quant Engine Streamlit UI.

Provides a dark theme with consistent colours, fonts, and styles
across all pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Theme:
    """Immutable theme definition with all UI colours and styles."""

    # ── Background colours ──────────────────────────────────────────
    background: str = "#0E1117"
    sidebar_bg: str = "#1A1D24"
    card_bg: str = "#1E2128"
    input_bg: str = "#262730"
    hover_bg: str = "#2A2D35"

    # ── Brand / accent ──────────────────────────────────────────────
    primary: str = "#1F77B4"
    primary_light: str = "#4A9ADE"
    primary_dark: str = "#155A8A"

    # ── Semantic colours ────────────────────────────────────────────
    success: str = "#00C853"
    danger: str = "#FF5252"
    warning: str = "#FFC107"
    info: str = "#2196F3"

    # ── Text ────────────────────────────────────────────────────────
    text: str = "#FFFFFF"
    text_secondary: str = "#9DA3B0"
    text_muted: str = "#6B7280"

    # ── Borders ─────────────────────────────────────────────────────
    border: str = "#2D3138"
    border_light: str = "#3A3F48"

    # ── Signal colours ──────────────────────────────────────────────
    signal_buy: str = "#00C853"
    signal_sell: str = "#FF5252"
    signal_hold: str = "#FFC107"
    signal_strong_buy: str = "#00E676"
    signal_strong_sell: str = "#FF1744"

    # ── Regime colours ──────────────────────────────────────────────
    regime_bull: str = "#00C853"
    regime_bear: str = "#FF5252"
    regime_sideways: str = "#9E9E9E"
    regime_panic: str = "#D50000"
    regime_overheated: str = "#FF6D00"
    regime_recovery: str = "#00BFA5"
    regime_accumulation: str = "#448AFF"
    regime_distribution: str = "#FFAB00"
    regime_low_volatility: str = "#78909C"
    regime_high_volatility: str = "#E040FB"

    # ── Fonts ───────────────────────────────────────────────────────
    font_family: str = "Inter, -apple-system, BlinkMacSystemFont, sans-serif"
    font_mono: str = "JetBrains Mono, Fira Code, monospace"

    # ── Sizing ───────────────────────────────────────────────────────
    border_radius: str = "8px"
    border_radius_sm: str = "4px"
    border_radius_lg: str = "12px"

    # ── Chart colours ───────────────────────────────────────────────
    chart_colors: list[str] = field(default_factory=lambda: [
        "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728",
        "#9467BD", "#8C564B", "#E377C2", "#7F7F7F",
        "#BCBD22", "#17BECF",
    ])

    def to_dict(self) -> dict[str, Any]:
        """Return theme as a flat dict for inline CSS or Streamlit config."""
        return {
            "primaryColor": self.primary,
            "backgroundColor": self.background,
            "secondaryBackgroundColor": self.card_bg,
            "textColor": self.text,
            "font": self.font_family,
        }

    def regime_color(self, regime: str) -> str:
        """Return the colour associated with a market regime label."""
        palette = {
            "BULL": self.regime_bull,
            "BEAR": self.regime_bear,
            "SIDEWAYS": self.regime_sideways,
            "PANIC": self.regime_panic,
            "OVERHEATED": self.regime_overheated,
            "RECOVERY": self.regime_recovery,
            "ACCUMULATION": self.regime_accumulation,
            "DISTRIBUTION": self.regime_distribution,
            "LOW_VOLATILITY": self.regime_low_volatility,
            "HIGH_VOLATILITY": self.regime_high_volatility,
            "UNKNOWN": self.text_muted,
        }
        return palette.get(regime.upper(), self.text_muted)

    def signal_color(self, signal: str) -> str:
        """Return the colour associated with a trade signal."""
        palette = {
            "STRONG_BUY": self.signal_strong_buy,
            "BUY": self.signal_buy,
            "HOLD": self.signal_hold,
            "SELL": self.signal_sell,
            "STRONG_SELL": self.signal_strong_sell,
        }
        return palette.get(signal.upper(), self.text_muted)


# ── Singleton ─────────────────────────────────────────────────────

theme = Theme()
