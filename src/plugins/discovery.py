"""Plugin auto-discovery for the NEPSE Quant Engine.

Scans the built-in ``src.plugins.builtin`` package and any plugin
directories configured on disk (``plugins/`` by default), instantiates
:class:`Plugin` subclasses, and registers them with the global
registry.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any

from src.logging.logger import logger
from src.plugins.base import Plugin
from src.plugins.registry import registry

_SKIP_MODULES = {"base", "registry", "discovery", "builtin"}


def discover_in_package(package: str) -> list[type[Plugin]]:
    """Find plugin classes inside a Python package.

    Args:
        package: Dotted package path.

    Returns:
        List of plugin classes defined in the package.
    """
    try:
        module = importlib.import_module(package)
    except ImportError as exc:
        logger.debug("Plugin package '%s' unavailable: %s", package, exc)
        return []

    found: list[type[Plugin]] = []
    for info in pkgutil.iter_modules(module.__path__):
        if info.name.startswith("_") or info.name in _SKIP_MODULES:
            continue
        try:
            sub = importlib.import_module(f"{package}.{info.name}")
            for _, cls in inspect.getmembers(sub, inspect.isclass):
                if (
                    issubclass(cls, Plugin)
                    and cls is not Plugin
                    and cls.__module__ == f"{package}.{info.name}"
                ):
                    found.append(cls)
        except Exception as exc:
            logger.warning(
                "Failed to scan plugin module '%s.%s': %s",
                package,
                info.name,
                exc,
            )
    return found


def discover_in_directory(directory: str | Path) -> list[type[Plugin]]:
    """Find plugin classes in Python modules under a directory.

    Args:
        directory: Directory containing plugin modules.

    Returns:
        List of plugin classes.
    """
    path = Path(directory)
    found: list[type[Plugin]] = []
    if not path.is_dir():
        return found

    for module_path in sorted(path.glob("*.py")):
        if module_path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                module_path.stem, module_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, Plugin) and cls is not Plugin:
                    found.append(cls)
        except Exception as exc:
            logger.warning(
                "Failed to load plugin file '%s': %s", module_path, exc
            )
    return found


def load_plugins(
    register: bool = True,
    plugin_dir: str | Path | None = None,
) -> list[Plugin]:
    """Discover and optionally register all plugins.

    Args:
        register: Whether to register discovered plugins with the
            global registry.
        plugin_dir: Optional on-disk plugin directory (defaults to
            ``plugins`` at the project root).

    Returns:
        List of instantiated plugins.
    """
    classes: list[type[Plugin]] = []
    classes.extend(discover_in_package("src.plugins.builtin"))
    classes.extend(discover_in_directory(plugin_dir or "plugins"))

    instances: list[Plugin] = []
    reg = registry() if register else None

    for cls in classes:
        try:
            instance = cls()
            instance.initialize()
            if reg is not None:
                if reg.has(instance.name):
                    continue
                reg.register(instance)
            instances.append(instance)
        except Exception as exc:
            logger.warning("Could not initialise plugin %s: %s", cls, exc)

    logger.info("Discovered %d plugin(s).", len(instances))
    return instances


def reload_plugins(plugin_dir: str | Path | None = None) -> int:
    """Clear and re-register all plugins.

    Args:
        plugin_dir: Optional on-disk plugin directory.

    Returns:
        Number of plugins registered.
    """
    registry().clear()
    load_plugins(plugin_dir=plugin_dir)
    return registry().count()
