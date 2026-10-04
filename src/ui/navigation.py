"""Navigation configuration for the NEPSE Quant Engine Streamlit UI.

Defines page labels, icons, and navigation helpers consumed by
:mod:`app.py` and the page modules.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Navigation items — ordered list of (label, icon)
# ---------------------------------------------------------------------------

NAV_ITEMS: Final[list[tuple[str, str]]] = [
    ("Dashboard", "dashboard"),
    ("Market Scanner", "scanner"),
    ("Stock Analysis", "analysis"),
    ("Recommendations", "recommendations"),
    ("Portfolio", "portfolio"),
    ("Portfolio Optimiser", "optimizer"),
    ("Risk Analysis", "risk"),
    ("Backtesting", "backtest"),
    ("Monte Carlo", "monte_carlo"),
    ("Walk Forward", "walk_forward"),
    ("Parameter Optimiser", "parameter_optimizer"),
    ("Adaptive Strategy", "adaptive_strategy"),
    ("Market Regime", "market_regime"),
    ("Watchlist", "watchlist"),
    ("Settings", "settings"),
]

PAGE_LABELS: Final[dict[str, str]] = {
    key: label for label, key in NAV_ITEMS
}

PAGE_ICONS: Final[dict[str, str]] = {
    "dashboard": "📊",
    "scanner": "🔍",
    "analysis": "📈",
    "recommendations": "💡",
    "portfolio": "💼",
    "optimizer": "⚡",
    "risk": "⚠️",
    "backtest": "🔄",
    "monte_carlo": "🎲",
    "walk_forward": "🏃",
    "parameter_optimizer": "🔧",
    "adaptive_strategy": "🧠",
    "market_regime": "🌦️",
    "watchlist": "👁️",
    "settings": "⚙️",
}

# Mapping page keys to the module path under src.ui.pages
PAGE_MODULES: Final[dict[str, str]] = {
    "dashboard": "src.ui.pages.dashboard_page",
    "scanner": "src.ui.pages.scanner_page",
    "analysis": "src.ui.pages.analysis_page",
    "recommendations": "src.ui.pages.recommendations_page",
    "portfolio": "src.ui.pages.portfolio_page",
    "optimizer": "src.ui.pages.optimizer_page",
    "risk": "src.ui.pages.risk_page",
    "backtest": "src.ui.pages.backtest_page",
    "monte_carlo": "src.ui.pages.monte_carlo_page",
    "walk_forward": "src.ui.pages.walk_forward_page",
    "parameter_optimizer": "src.ui.pages.parameter_optimizer_page",
    "adaptive_strategy": "src.ui.pages.adaptive_strategy_page",
    "market_regime": "src.ui.pages.market_regime_page",
    "watchlist": "src.ui.pages.watchlist_page",
    "settings": "src.ui.pages.settings_page",
}


def get_page_index(page_key: str) -> int:
    """Return the index of *page_key* in the navigation list.

    Args:
        page_key: The key string for the page (e.g. ``\"dashboard\"``).

    Returns:
        Zero-based index, or ``0`` if not found.
    """
    for idx, (_label, key) in enumerate(NAV_ITEMS):
        if key == page_key:
            return idx
    return 0
