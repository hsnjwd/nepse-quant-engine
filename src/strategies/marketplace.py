"""Strategy Marketplace — auto-discovery of pluggable strategies.

Scans the strategies package for classes deriving from
:class:`BaseStrategy`, collects their metadata (author, tags, version,
description), and registers them with the global strategy registry.

New strategies can be added by dropping a module into
``src/strategies/`` that subclasses ``BaseStrategy`` — no registry
changes required.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from src.logging.logger import logger
from src.strategies.base import BaseStrategy
from src.strategies.registry import registry

# Modules skipped during discovery (infrastructure, not strategies).
_SKIP_MODULES = {"base", "registry", "manager", "aggregator", "marketplace"}


def discover_strategies(
    package: str = "src.strategies",
) -> list[type[BaseStrategy]]:
    """Discover all strategy classes in a package.

    Args:
        package: Dotted package path to scan.

    Returns:
        List of strategy classes (subclasses of ``BaseStrategy``).
    """
    module = importlib.import_module(package)
    found: list[type[BaseStrategy]] = []

    for info in pkgutil.iter_modules(module.__path__):
        if info.name.startswith("_") or info.name in _SKIP_MODULES:
            continue
        try:
            sub = importlib.import_module(f"{package}.{info.name}")
            for _, cls in inspect.getmembers(sub, inspect.isclass):
                if (
                    issubclass(cls, BaseStrategy)
                    and cls is not BaseStrategy
                    and cls.__module__ == f"{package}.{info.name}"
                ):
                    found.append(cls)
        except Exception as exc:
            logger.warning(
                "Failed to scan strategy module '%s.%s': %s",
                package,
                info.name,
                exc,
            )

    logger.info("Marketplace discovered %d strategy classes.", len(found))
    return found


def strategy_metadata() -> list[dict[str, Any]]:
    """Return metadata for all marketplace strategies.

    Returns:
        List of dicts with ``name``, ``author``, ``tags``, ``version``
        and ``description``.
    """
    metadata: list[dict[str, Any]] = []
    for cls in discover_strategies():
        try:
            instance = cls()
            metadata.append(
                {
                    "name": instance.name,
                    "author": getattr(cls, "AUTHOR", "Unknown"),
                    "tags": list(getattr(cls, "TAGS", ())),
                    "version": instance.version,
                    "description": instance.description,
                }
            )
        except Exception as exc:
            logger.warning("Could not build metadata for %s: %s", cls, exc)
    return sorted(metadata, key=lambda item: item["name"])


def register_marketplace() -> int:
    """Register every discovered strategy with the global registry.

    Strategies already registered are skipped without error.

    Returns:
        Number of newly registered strategies.
    """
    count = 0
    for cls in discover_strategies():
        try:
            instance = cls()
            reg = registry()
            if reg.has(instance.name):
                continue
            reg.register(instance)
            count += 1
            logger.info("Marketplace registered '%s'.", instance.name)
        except Exception as exc:
            logger.warning("Could not register %s: %s", cls, exc)
    return count


def marketplace_info() -> dict[str, Any]:
    """Return a full marketplace report.

    Returns:
        Dict with discovered metadata and registration status.
    """
    from src.strategies.registry import list_strategies

    metadata = strategy_metadata()
    return {
        "strategies": metadata,
        "registered": list_strategies(),
        "count": len(metadata),
    }
