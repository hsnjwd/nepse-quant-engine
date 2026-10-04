"""Plugin system for the NEPSE Quant Engine.

Plugins extend the platform with custom indicators, reports,
strategies, alerts, exporters, notification providers, and broker
adapters.  They are discovered automatically and registered in a
thread-safe singleton registry.

Usage::

    from src.plugins import load_plugins, registry

    load_plugins()
    metadata = registry().metadata()
"""

from __future__ import annotations

from src.plugins.base import (
    AlertPlugin,
    BrokerPlugin,
    ExporterPlugin,
    IndicatorPlugin,
    NotificationPlugin,
    Plugin,
    PluginType,
    ReportPlugin,
    StrategyPlugin,
)
from src.plugins.discovery import (
    discover_in_directory,
    discover_in_package,
    load_plugins,
    reload_plugins,
)
from src.plugins.registry import (
    PluginRegistry,
    get_plugin,
    plugin_metadata,
    plugins_of_type,
    register_plugin,
    registry,
)

__all__ = [
    "AlertPlugin",
    "BrokerPlugin",
    "ExporterPlugin",
    "IndicatorPlugin",
    "NotificationPlugin",
    "Plugin",
    "PluginType",
    "ReportPlugin",
    "StrategyPlugin",
    "discover_in_directory",
    "discover_in_package",
    "load_plugins",
    "reload_plugins",
    "PluginRegistry",
    "get_plugin",
    "plugin_metadata",
    "plugins_of_type",
    "register_plugin",
    "registry",
]
