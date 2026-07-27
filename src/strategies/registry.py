"""Central strategy registry and discovery manager."""

from __future__ import annotations

from typing import Iterator

from src.strategies.base import BaseStrategy
from src.strategies.breakout import BreakoutStrategy
from src.strategies.momentum import MomentumStrategy


class StrategyRegistry:
    """Registry for managing quantitative trading strategies."""

    def __init__(self) -> None:
        """Initialize an empty strategy registry."""
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        """Register a strategy instance.

        Args:
            strategy:
                Strategy instance.

        Raises:
            TypeError:
                If strategy is not derived from BaseStrategy.
            ValueError:
                If the strategy has already been registered.
        """
        if not isinstance(strategy, BaseStrategy):
            raise TypeError(
                f"Expected BaseStrategy instance, got {type(strategy).__name__}"
            )

        key = strategy.name.strip().lower()

        if key in self._strategies:
            raise ValueError(
                f"Strategy '{strategy.name}' is already registered."
            )

        self._strategies[key] = strategy

    def unregister(self, name: str) -> None:
        """Remove a strategy from the registry.

        Args:
            name:
                Strategy name.

        Raises:
            KeyError:
                If strategy does not exist.
        """
        key = name.strip().lower()

        if key not in self._strategies:
            raise KeyError(f"Strategy '{name}' is not registered.")

        del self._strategies[key]

    def get(self, name: str) -> BaseStrategy:
        """Return a registered strategy.

        Args:
            name:
                Strategy name.

        Returns:
            Registered strategy instance.

        Raises:
            KeyError:
                If the strategy is not registered.
        """
        key = name.strip().lower()

        try:
            return self._strategies[key]
        except KeyError as exc:
            available = ", ".join(self.list_strategies())
            raise KeyError(
                f"Strategy '{name}' not found in registry. "
                f"Available strategies: [{available}]"
            ) from exc

    def has(self, name: str) -> bool:
        """Return True if a strategy is registered."""
        return name.strip().lower() in self._strategies

    def list_strategies(self) -> list[str]:
        """Return strategy names sorted alphabetically."""
        return sorted(
            strategy.name
            for strategy in self._strategies.values()
        )

    def strategy_info(self) -> list[dict[str, str]]:
        """Return metadata for all registered strategies."""
        return sorted(
            (
                {
                    "name": strategy.name,
                    "version": strategy.version,
                    "description": strategy.description,
                }
                for strategy in self._strategies.values()
            ),
            key=lambda item: item["name"],
        )

    def clear(self) -> None:
        """Remove all registered strategies."""
        self._strategies.clear()

    def __contains__(self, name: str) -> bool:
        """Support 'strategy in registry' syntax."""
        return self.has(name)

    def __len__(self) -> int:
        """Return number of registered strategies."""
        return len(self._strategies)

    def __iter__(self) -> Iterator[BaseStrategy]:
        """Iterate over registered strategies."""
        return iter(
            sorted(
                self._strategies.values(),
                key=lambda strategy: strategy.name,
            )
        )

    def __repr__(self) -> str:
        """Return developer-friendly representation."""
        return (
            f"StrategyRegistry("
            f"{len(self)} strategies)"
        )


_registry = StrategyRegistry()

_registry.register(MomentumStrategy())
_registry.register(BreakoutStrategy())


def register_strategy(strategy: BaseStrategy) -> None:
    """Register a strategy globally."""
    _registry.register(strategy)


def unregister_strategy(name: str) -> None:
    """Remove a strategy from the global registry."""
    _registry.unregister(name)


def get_strategy(name: str) -> BaseStrategy:
    """Return a registered strategy."""
    return _registry.get(name)


def has_strategy(name: str) -> bool:
    """Return True if a strategy exists."""
    return _registry.has(name)


def list_strategies() -> list[str]:
    """Return all registered strategy names."""
    return _registry.list_strategies()


def strategy_info() -> list[dict[str, str]]:
    """Return metadata for all registered strategies."""
    return _registry.strategy_info()


def registry() -> StrategyRegistry:
    """Return the singleton strategy registry."""
    return _registry

__all__ = [
    "StrategyRegistry",
    "register_strategy",
    "unregister_strategy",
    "get_strategy",
    "has_strategy",
    "list_strategies",
    "strategy_info",
    "registry",
]