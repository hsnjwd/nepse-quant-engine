"""Theme manager — professional theme system with multiple presets.

Supports:
- Dark (default)
- Light
- TradingView inspired
- Bloomberg inspired

Automatically saves preference via UserSettings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ui.theme import Theme as BaseTheme


@dataclass(frozen=True)
class ThemeVariant:
    """A named theme variant with all colour overrides."""

    name: str = "Dark"
    background: str = "#0E1117"
    sidebar_bg: str = "#1A1D24"
    card_bg: str = "#1E2128"
    input_bg: str = "#262730"
    hover_bg: str = "#2A2D35"
    primary: str = "#1F77B4"
    success: str = "#00C853"
    danger: str = "#FF5252"
    warning: str = "#FFC107"
    info: str = "#2196F3"
    text: str = "#FFFFFF"
    text_secondary: str = "#9DA3B0"
    text_muted: str = "#6B7280"
    border: str = "#2D3138"
    border_light: str = "#3A3F48"

    chart_colors: list[str] = field(default_factory=lambda: [
        "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728",
        "#9467BD", "#8C564B", "#E377C2", "#7F7F7F",
        "#BCBD22", "#17BECF",
    ])

    def apply(self, theme: BaseTheme) -> dict[str, str]:
        """Return colour overrides compatible with the base Theme.

        Since ``BaseTheme`` (:class:`Theme`) is frozen, this method
        returns a dict of overrides instead of mutating the instance.
        """
        return {
            "background": self.background,
            "sidebar_bg": self.sidebar_bg,
            "card_bg": self.card_bg,
            "input_bg": self.input_bg,
            "hover_bg": self.hover_bg,
            "primary": self.primary,
            "success": self.success,
            "danger": self.danger,
            "warning": self.warning,
            "info": self.info,
            "text": self.text,
            "text_secondary": self.text_secondary,
            "text_muted": self.text_muted,
            "border": self.border,
            "border_light": self.border_light,
        }

    def to_streamlit_config(self) -> dict[str, str]:
        """Return Streamlit-compatible theme config."""
        return {
            "primaryColor": self.primary,
            "backgroundColor": self.background,
            "secondaryBackgroundColor": self.card_bg,
            "textColor": self.text,
            "font": "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
        }

    def to_css_variables(self) -> str:
        """Generate CSS custom properties for the theme variant."""
        return f"""
        :root {{
            --background: {self.background};
            --sidebar-bg: {self.sidebar_bg};
            --card-bg: {self.card_bg};
            --input-bg: {self.input_bg};
            --hover-bg: {self.hover_bg};
            --primary: {self.primary};
            --success: {self.success};
            --danger: {self.danger};
            --warning: {self.warning};
            --info: {self.info};
            --text: {self.text};
            --text-secondary: {self.text_secondary};
            --text-muted: {self.text_muted};
            --border: {self.border};
            --border-light: {self.border_light};
        }}
        """


# ── Built-in theme variants ──────────────────────────────────────

THEMES: dict[str, ThemeVariant] = {
    "dark": ThemeVariant(
        name="Dark",
        background="#0E1117",
        sidebar_bg="#1A1D24",
        card_bg="#1E2128",
        text="#FFFFFF",
        text_secondary="#9DA3B0",
        text_muted="#6B7280",
        border="#2D3138",
    ),
    "light": ThemeVariant(
        name="Light",
        background="#FAFAFA",
        sidebar_bg="#FFFFFF",
        card_bg="#F5F5F5",
        input_bg="#EEEEEE",
        hover_bg="#E8E8E8",
        primary="#1F77B4",
        success="#2E7D32",
        danger="#C62828",
        warning="#F9A825",
        info="#1565C0",
        text="#212121",
        text_secondary="#616161",
        text_muted="#9E9E9E",
        border="#E0E0E0",
        border_light="#BDBDBD",
        chart_colors=["#1F77B4", "#FF7F0E", "#2CA02C", "#D62728",
                       "#9467BD", "#8C564B", "#E377C2", "#7F7F7F",
                       "#BCBD22", "#17BECF"],
    ),
    "tradingview": ThemeVariant(
        name="TradingView",
        background="#131722",
        sidebar_bg="#1E222D",
        card_bg="#1E222D",
        input_bg="#2A2E39",
        hover_bg="#2A2E39",
        primary="#2962FF",
        success="#089981",
        danger="#F23645",
        warning="#FF9800",
        info="#2962FF",
        text="#D1D4DC",
        text_secondary="#787B86",
        text_muted="#5B5F6A",
        border="#2A2E39",
        border_light="#363A45",
        chart_colors=["#2962FF", "#089981", "#F23645", "#FF9800",
                       "#9B59B6", "#E91E63", "#00BCD4", "#8BC34A",
                       "#FF5722", "#607D8B"],
    ),
    "bloomberg": ThemeVariant(
        name="Bloomberg",
        background="#000000",
        sidebar_bg="#111111",
        card_bg="#1A1A1A",
        input_bg="#222222",
        hover_bg="#2A2A2A",
        primary="#FF6600",
        success="#00FF00",
        danger="#FF0000",
        warning="#FFFF00",
        info="#00BFFF",
        text="#FFFFFF",
        text_secondary="#AAAAAA",
        text_muted="#666666",
        border="#333333",
        border_light="#444444",
        chart_colors=["#FF6600", "#00FF00", "#FF0000", "#FFFF00",
                       "#00BFFF", "#FF00FF", "#00FFFF", "#FFFFFF",
                       "#FFD700", "#FF4500"],
    ),
}


class ThemeManager:
    """Manages theme selection and application.

    Usage::
        mgr = ThemeManager()
        current = mgr.get_current_theme()  # Returns ThemeVariant
        mgr.set_theme("tradingview")
        css = mgr.get_css_variables()
    """

    _instance: ThemeManager | None = None

    def __new__(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialised"):
            return
        self._current: str = "dark"
        self._variant: str = "default"
        self._load_preference()
        self._initialised = True

    def _load_preference(self) -> None:
        """Load saved theme preference from UserSettings."""
        try:
            from src.config.user_settings import UserSettings
            settings = UserSettings()
            self._current = settings.get("theme", "dark")
            self._variant = settings.get("theme_variant", "default")
        except Exception:
            self._current = "dark"
            self._variant = "default"

    def _save_preference(self) -> None:
        """Save theme preference to UserSettings."""
        try:
            from src.config.user_settings import UserSettings
            settings = UserSettings()
            settings.set("theme", self._current)
            settings.set("theme_variant", self._variant)
        except Exception:
            pass

    @property
    def available_themes(self) -> list[str]:
        """Return list of available theme names."""
        return list(THEMES.keys())

    @property
    def available_variants(self) -> list[str]:
        """Return list of variant names for current theme."""
        return ["default", "colorblind", "high_contrast"]

    @property
    def current_theme_name(self) -> str:
        return self._current

    @property
    def current_variant(self) -> str:
        return self._variant

    def get_current_theme(self) -> ThemeVariant:
        """Get the current active theme variant."""
        return THEMES.get(self._current, THEMES["dark"])

    def set_theme(self, name: str) -> bool:
        """Set the active theme by name. Returns True on success."""
        if name in THEMES:
            self._current = name
            self._save_preference()
            return True
        return False

    def set_variant(self, variant: str) -> None:
        """Set the active variant for the current theme."""
        self._variant = variant
        self._save_preference()

    def get_css_variables(self) -> str:
        """Generate CSS custom properties for the current theme."""
        theme = self.get_current_theme()
        return theme.to_css_variables()

    def streamlit_config(self) -> dict[str, str]:
        """Get Streamlit theme config for the current theme."""
        return self.get_current_theme().to_streamlit_config()


# ── Singleton ─────────────────────────────────────────────────────

theme_manager = ThemeManager()
