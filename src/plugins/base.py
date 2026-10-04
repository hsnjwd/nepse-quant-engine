"""Plugin base classes for the NEPSE Quant Engine.

Plugins extend the platform for indicators, reports, strategies,
alerts, exporters, notification providers, and broker adapters.
Each plugin subclasses :class:`Plugin` and is discovered
automatically by :mod:`src.plugins.discovery`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger("nepse.plugins")


class PluginType(str, Enum):
    """Supported plugin categories."""

    INDICATOR = "indicator"
    REPORT = "report"
    STRATEGY = "strategy"
    ALERT = "alert"
    EXPORTER = "exporter"
    NOTIFICATION = "notification"
    BROKER = "broker"


class Plugin(ABC):
    """Base class for all plugins.

    Attributes:
        name: Unique plugin name.
        version: Plugin version string.
        author: Plugin author.
        description: Plugin description.
        plugin_type: Plugin category.
    """

    name: str = "base_plugin"
    version: str = "1.0.0"
    author: str = "Unknown"
    description: str = ""
    plugin_type: PluginType = PluginType.INDICATOR

    def __init__(self) -> None:
        """Validate plugin identity."""
        if not self.name or self.name == "base_plugin":
            raise ValueError("Plugin must define a unique 'name'.")

    @abstractmethod
    def initialize(self) -> None:
        """Perform any startup work for the plugin.

        Raises:
            Exception: When the plugin cannot initialise.
        """
        raise NotImplementedError

    def shutdown(self) -> None:
        """Perform any cleanup work for the plugin."""
        logger.debug("Plugin '%s' shutdown.", self.name)

    def metadata(self) -> dict[str, Any]:
        """Return plugin metadata.

        Returns:
            JSON-serialisable metadata dict.
        """
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "type": self.plugin_type.value,
        }


class IndicatorPlugin(Plugin):
    """Base class for custom indicator plugins."""

    plugin_type = PluginType.INDICATOR

    @abstractmethod
    def compute(self, df: Any, **params: Any) -> Any:
        """Compute the indicator and return an enriched DataFrame.

        Args:
            df: OHLCV DataFrame.
            **params: Indicator parameters.

        Returns:
            DataFrame with the indicator column(s) appended.
        """
        raise NotImplementedError


class ReportPlugin(Plugin):
    """Base class for custom report plugins."""

    plugin_type = PluginType.REPORT

    @abstractmethod
    def render(self, data: dict[str, Any]) -> str:
        """Render a report section as markdown.

        Args:
            data: Report input data.

        Returns:
            Markdown string.
        """
        raise NotImplementedError


class StrategyPlugin(Plugin):
    """Base class for custom strategy plugins."""

    plugin_type = PluginType.STRATEGY

    @abstractmethod
    def build(self) -> Any:
        """Return a :class:`BaseStrategy` instance."""
        raise NotImplementedError


class AlertPlugin(Plugin):
    """Base class for custom alert plugins."""

    plugin_type = PluginType.ALERT

    @abstractmethod
    def check(self, analysis: dict[str, Any]) -> list[dict[str, Any]]:
        """Evaluate alert rules against an analysis payload.

        Args:
            analysis: Analysis dict from the analyzer engine.

        Returns:
            List of alert dicts.
        """
        raise NotImplementedError


class ExporterPlugin(Plugin):
    """Base class for custom export format plugins."""

    plugin_type = PluginType.EXPORTER

    @abstractmethod
    def export(self, data: Any, path: str) -> str:
        """Export data to the plugin's format.

        Args:
            data: Payload to export.
            path: Destination file path.

        Returns:
            The file path written.
        """
        raise NotImplementedError


class NotificationPlugin(Plugin):
    """Base class for custom notification providers."""

    plugin_type = PluginType.NOTIFICATION

    @abstractmethod
    def send(self, message: str, **kwargs: Any) -> bool:
        """Send a notification.

        Args:
            message: Notification text.
            **kwargs: Provider-specific options.

        Returns:
            Whether delivery succeeded.
        """
        raise NotImplementedError


class BrokerPlugin(Plugin):
    """Base class for broker adapter plugins."""

    plugin_type = PluginType.BROKER

    @abstractmethod
    def connect(self) -> None:
        """Connect to the broker."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """Place an order.

        Args:
            order: Order dict.

        Returns:
            Order acknowledgement dict.
        """
        raise NotImplementedError
