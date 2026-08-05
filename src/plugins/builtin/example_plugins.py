"""Example built-in plugins demonstrating the plugin API.

Includes one plugin per supported category so the discovery pipeline,
registry, and plugin manager UI have working examples.
"""

from __future__ import annotations

from typing import Any

from src.plugins.base import (
    AlertPlugin,
    ExporterPlugin,
    IndicatorPlugin,
    NotificationPlugin,
    ReportPlugin,
    StrategyPlugin,
)


class VolumeRatioIndicatorPlugin(IndicatorPlugin):
    """Indicator plugin adding a simple volume-ratio column."""

    name = "volume_ratio_indicator"
    version = "1.0.0"
    author = "NEPSE Quant Engine"
    description = "Adds VOLUME_RATIO_20 (volume / 20-day average)."

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def compute(self, df: Any, **params: Any) -> Any:
        """Add the volume ratio column."""
        result = df.copy()
        period = int(params.get("period", 20))
        average = result["Volume"].rolling(period).mean()
        result["VOLUME_RATIO_20"] = result["Volume"] / average.replace(0, float("nan"))
        return result


class MarketPulseReportPlugin(ReportPlugin):
    """Report plugin rendering a market pulse section."""

    name = "market_pulse_report"
    version = "1.0.0"
    author = "NEPSE Quant Engine"
    description = "Renders a one-paragraph market pulse report section."

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def render(self, data: dict[str, Any]) -> str:
        """Render the report section."""
        index = data.get("index")
        change = data.get("change_pct")
        regime = data.get("regime", "UNKNOWN")
        if index is None or change is None:
            return "### Market Pulse\nNo market data available."
        return (
            f"### Market Pulse\n"
            f"The index sits at **{index:,.2f}** ({change:+.2f}% today) "
            f"in a **{regime}** regime."
        )


class PriceAlertPlugin(AlertPlugin):
    """Alert plugin flagging RSI extremes."""

    name = "rsi_extreme_alerts"
    version = "1.0.0"
    author = "NEPSE Quant Engine"
    description = "Alerts when RSI leaves the 30–70 comfort zone."

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def check(self, analysis: dict[str, Any]) -> list[dict[str, Any]]:
        """Check RSI extremes."""
        rsi = analysis.get("rsi")
        if rsi is None:
            return []
        alerts: list[dict[str, Any]] = []
        if rsi < 30:
            alerts.append(
                {"severity": "warning", "message": f"RSI oversold at {rsi:.1f}"}
            )
        elif rsi > 70:
            alerts.append(
                {"severity": "warning", "message": f"RSI overbought at {rsi:.1f}"}
            )
        return alerts


class CsvExporterPlugin(ExporterPlugin):
    """Exporter plugin producing CSV exports."""

    name = "csv_exporter"
    version = "1.0.0"
    author = "NEPSE Quant Engine"
    description = "Exports tabular data to CSV."

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def export(self, data: Any, path: str) -> str:
        """Write data (DataFrame or list of dicts) to CSV."""
        import pandas as pd

        frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        frame.to_csv(path, index=False)
        return path


class ConsoleNotificationPlugin(NotificationPlugin):
    """Notification plugin logging messages to the console."""

    name = "console_notifier"
    version = "1.0.0"
    author = "NEPSE Quant Engine"
    description = "Logs notification messages."

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def send(self, message: str, **kwargs: Any) -> bool:
        """Log the message."""
        import logging

        logging.getLogger("nepse.plugins.notification").info(
            "[NotificationPlugin] %s", message
        )
        return True


class MovingAverageStrategyPlugin(StrategyPlugin):
    """Strategy plugin wrapping the built-in momentum strategy."""

    name = "momentum_strategy_plugin"
    version = "1.0.0"
    author = "NEPSE Quant Engine"
    description = "Provides the momentum strategy through the plugin API."

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def build(self) -> Any:
        """Return a strategy instance."""
        from src.strategies.momentum import MomentumStrategy

        return MomentumStrategy()
