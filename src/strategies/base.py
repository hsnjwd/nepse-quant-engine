"""Abstract base class for quantitative trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class SignalType(str, Enum):
    """Supported trading signal types."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class BaseStrategy(ABC):
    """Abstract base class defining the quantitative strategy interface."""

    VERSION = "1.0.0"

    def __init__(
        self,
        name: str,
        description: str,
        version: str | None = None,
    ) -> None:
        """Initialize the base strategy.

        Args:
            name:
                Human-readable strategy name.
            description:
                Strategy description.
            version:
                Optional strategy version. Defaults to VERSION.
        """
        self._name = name.strip()
        self._description = description.strip()
        self._version = version or self.VERSION

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return self._name

    @property
    def description(self) -> str:
        """Return the strategy description."""
        return self._description

    @property
    def version(self) -> str:
        """Return the strategy version."""
        return self._version

    @abstractmethod
    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate a trading signal.

        Args:
            df:
                Historical OHLCV market data.

        Returns:
            Standardized signal payload.
        """
        raise NotImplementedError

    def build_signal_payload(
        self,
        signal: SignalType | str,
        price: float,
        stop_loss: float | None = None,
        target1: float | None = None,
        targets: list[float] | None = None,
        score: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Construct a standardized signal payload.

        This method supports both the legacy single-target format
        (``target1``) and the newer multi-target format (``targets``).

        Args:
            signal:
                BUY, SELL or HOLD.
            price:
                Reference execution price.
            stop_loss:
                Suggested stop-loss level.
            target1:
                Legacy first profit target.
            targets:
                Optional ordered list of profit targets.
            score:
                Strategy confidence score.
            details:
                Additional strategy metadata.

        Returns:
            Normalized signal dictionary.
        """
        if isinstance(signal, SignalType):
            signal = signal.value

        if targets is None:
            targets = []

        # Keep backward compatibility with existing engine/tests.
        if target1 is not None:
            if not targets:
                targets = [target1]
            else:
                targets[0] = target1

        rounded_targets = [round(float(t), 2) for t in targets]

        payload = {
            "signal": signal,
            "price": round(float(price), 2),
            "stop_loss": (
                round(float(stop_loss), 2)
                if stop_loss is not None
                else None
            ),

            # Legacy compatibility
            "target1": (
                rounded_targets[0]
                if rounded_targets
                else None
            ),

            # New multi-target API
            "targets": rounded_targets,

            "score": round(float(score), 2),
            "strategy_name": self.name,
            "strategy_version": self.version,
            "details": details or {},
        }

        return payload

    def __repr__(self) -> str:
        """Return developer-friendly representation."""
        return (
            f"{self.__class__.__name__}("
            f"name='{self.name}', "
            f"version='{self.version}')"
        )