"""Plugin registry for the NEPSE Quant Engine.

A thread-safe singleton registry that stores discovered plugin
instances and provides typed lookups by category.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from src.plugins.base import Plugin, PluginType

logger = logging.getLogger("nepse.plugins.registry")


class PluginRegistry:
    """Registry for plugin instances.

    Usage::

        reg = PluginRegistry()
        reg.register(plugin)
        plugins = reg.plugins_of_type(PluginType.INDICATOR)
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._plugins: dict[str, Plugin] = {}
        self._disabled: set[str] = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: Plugin) -> None:
        """Register a plugin instance.

        Args:
            plugin: Plugin instance.

        Raises:
            TypeError: If not a Plugin.
            ValueError: If the plugin name is already registered.
        """
        if not isinstance(plugin, Plugin):
            raise TypeError(
                f"Expected Plugin, got {type(plugin).__name__}."
            )
        with self._lock:
            if plugin.name in self._plugins:
                raise ValueError(
                    f"Plugin '{plugin.name}' is already registered."
                )
            self._plugins[plugin.name] = plugin
            logger.info("Registered plugin '%s' (%s).", plugin.name, plugin.plugin_type.value)

    def unregister(self, name: str) -> None:
        """Remove a plugin by name.

        Args:
            name: Plugin name.

        Raises:
            KeyError: If not registered.
        """
        with self._lock:
            plugin = self._plugins.pop(name, None)
            if plugin is None:
                raise KeyError(f"Plugin '{name}' is not registered.")
            plugin.shutdown()

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, name: str) -> Plugin:
        """Return a plugin by name.

        Args:
            name: Plugin name.

        Returns:
            The plugin instance.

        Raises:
            KeyError: If not registered.
        """
        with self._lock:
            try:
                return self._plugins[name]
            except KeyError as exc:
                raise KeyError(
                    f"Plugin '{name}' not found. "
                    f"Available: {sorted(self._plugins)}"
                ) from exc

    def has(self, name: str) -> bool:
        """Return True if a plugin is registered."""
        with self._lock:
            return name in self._plugins

    def is_enabled(self, name: str) -> bool:
        """Return True when a registered plugin is enabled.

        Unknown plugins count as disabled.
        """
        with self._lock:
            return name in self._plugins and name not in self._disabled

    def enable(self, name: str) -> bool:
        """Re-enable a plugin after it was disabled.

        Args:
            name: Plugin name.

        Returns:
            True when the plugin is registered and was re-enabled.
        """
        with self._lock:
            if name not in self._plugins:
                return False
            self._disabled.discard(name)
            return True

    def disable(self, name: str) -> bool:
        """Disable a plugin without unregistering it.

        Disabled plugins remain discoverable but are excluded from
        ``enabled_all()`` and ``enabled_metadata()``.

        Args:
            name: Plugin name.

        Returns:
            True when the plugin is registered and was disabled.
        """
        with self._lock:
            if name not in self._plugins:
                return False
            self._disabled.add(name)
            return True

    def disabled_names(self) -> list[str]:
        """Return the names of currently disabled plugins."""
        with self._lock:
            return sorted(self._disabled)

    def plugins_of_type(self, plugin_type: PluginType | str) -> list[Plugin]:
        """Return all plugins of a category.

        Args:
            plugin_type: Plugin category.

        Returns:
            List of matching plugin instances.
        """
        if isinstance(plugin_type, str):
            plugin_type = PluginType(plugin_type)
        with self._lock:
            return [
                p for p in self._plugins.values() if p.plugin_type == plugin_type
            ]

    def all(self) -> list[Plugin]:
        """Return every registered plugin."""
        with self._lock:
            return list(self._plugins.values())

    def enabled_all(self) -> list[Plugin]:
        """Return every registered plugin that is not disabled."""
        with self._lock:
            return [
                p for p in self._plugins.values() if p.name not in self._disabled
            ]

    def enabled_metadata(self) -> list[dict[str, Any]]:
        """Return metadata for all enabled plugins."""
        with self._lock:
            return [
                p.metadata()
                for p in self._plugins.values()
                if p.name not in self._disabled
            ]

    def metadata(self) -> list[dict[str, Any]]:
        """Return metadata for every registered plugin."""
        with self._lock:
            return [p.metadata() for p in self._plugins.values()]

    def count(self) -> int:
        """Return the number of registered plugins."""
        with self._lock:
            return len(self._plugins)

    def clear(self) -> None:
        """Unregister every plugin."""
        with self._lock:
            for name in list(self._plugins):
                try:
                    self._plugins[name].shutdown()
                except Exception:  # pragma: no cover - defensive
                    pass
            self._plugins.clear()


_registry = PluginRegistry()


def registry() -> PluginRegistry:
    """Return the global plugin registry singleton."""
    return _registry


def register_plugin(plugin: Plugin) -> None:
    """Register a plugin globally."""
    _registry.register(plugin)


def get_plugin(name: str) -> Plugin:
    """Return a globally registered plugin by name."""
    return _registry.get(name)


def plugins_of_type(plugin_type: PluginType | str) -> list[Plugin]:
    """Return globally registered plugins of a category."""
    return _registry.plugins_of_type(plugin_type)


def plugin_metadata() -> list[dict[str, Any]]:
    """Return metadata for all globally registered plugins."""
    return _registry.metadata()
