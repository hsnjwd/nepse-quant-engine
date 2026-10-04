"""Slippage models for the institutional backtesting engine.

Professional execution simulation (Part 9.3).  Each model implements
:meth:`compute` which returns the adverse price adjustment for an
order given the current bar and execution context.  Models are
pluggable: add a new subclass and register it in ``SLIPPAGE_MODELS``.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from typing import Any

from src.backtesting.models import Bar, Order

logger = logging.getLogger(__name__)


class SlippageModel(ABC):
    """Abstract slippage model.

    Subclasses must implement :meth:`compute`.  The result is added
    to (for buys) or subtracted from (for sells) the base execution
    price.
    """

    name: str = "base"

    @abstractmethod
    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        """Return the adverse slippage amount (positive).

        Args:
            order: The order being executed.
            bar: Current market bar.
            base_price: Reference price (e.g. open or close).
            context: Optional execution context (e.g. volume traded).
        """
        raise NotImplementedError

    def adjust(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        """Return the final execution price after slippage.

        Buys pay more (base + slippage); sells receive less (base - slippage).
        """
        slip = self.compute(order, bar, base_price, context)
        if order.side.value == "BUY":
            return base_price + slip
        return base_price - slip


class FixedSlippage(SlippageModel):
    """Fixed absolute slippage per share."""

    name = "fixed"

    def __init__(self, amount: float = 0.05) -> None:
        """Initialise with a fixed per-share amount."""
        self.amount = max(0.0, float(amount))

    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        return self.amount


class PercentageSlippage(SlippageModel):
    """Slippage as a fraction of the base price."""

    name = "percentage"

    def __init__(self, rate: float = 0.001) -> None:
        """Initialise with a fractional rate (0.001 = 0.1%)."""
        self.rate = max(0.0, float(rate))

    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        return base_price * self.rate


class VolumeSlippage(SlippageModel):
    """Slippage grows with the fraction of bar volume consumed."""

    name = "volume"

    def __init__(self, base_rate: float = 0.0005, volume_factor: float = 1.0) -> None:
        """Initialise with a base rate and a participation factor."""
        self.base_rate = max(0.0, float(base_rate))
        self.volume_factor = max(0.0, float(volume_factor))

    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        qty = float(context.get("quantity", order.quantity) if context else order.quantity)
        bar_vol = float(bar.volume) if bar.volume > 0 else qty
        participation = min(qty / bar_vol, 1.0)
        return base_price * self.base_rate * (1.0 + self.volume_factor * participation)


class VolatilitySlippage(SlippageModel):
    """Slippage proportional to bar volatility (range / mid)."""

    name = "volatility"

    def __init__(self, vol_factor: float = 0.05) -> None:
        """Initialise with a volatility multiplier."""
        self.vol_factor = max(0.0, float(vol_factor))

    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        mid = (bar.high + bar.low) / 2.0 if (bar.high + bar.low) > 0 else base_price
        if mid <= 0:
            return 0.0
        intraday_range = (bar.high - bar.low) / mid
        return mid * self.vol_factor * max(intraday_range, 0.0)


class SpreadSlippage(SlippageModel):
    """Slippage equal to half the quoted spread."""

    name = "spread"

    def __init__(self, spread_pct: float = 0.002) -> None:
        """Initialise with a spread as a fraction of price."""
        self.spread_pct = max(0.0, float(spread_pct))

    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        return base_price * self.spread_pct / 2.0


class RandomizedSlippage(SlippageModel):
    """Slippage drawn from a uniform distribution around a base rate."""

    name = "random"

    def __init__(self, base_rate: float = 0.001, max_rate: float = 0.005, seed: int | None = None) -> None:
        """Initialise with a base rate and an upper bound."""
        self.base_rate = max(0.0, float(base_rate))
        self.max_rate = max(self.base_rate, float(max_rate))
        self._rng = random.Random(seed)

    def compute(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        rate = self._rng.uniform(self.base_rate, self.max_rate)
        return base_price * rate


# Registry of available models by name.
SLIPPAGE_MODELS: dict[str, type[SlippageModel]] = {
    FixedSlippage.name: FixedSlippage,
    PercentageSlippage.name: PercentageSlippage,
    VolumeSlippage.name: VolumeSlippage,
    VolatilitySlippage.name: VolatilitySlippage,
    SpreadSlippage.name: SpreadSlippage,
    RandomizedSlippage.name: RandomizedSlippage,
}


def build_slippage_model(
    name: str = "fixed",
    **params: Any,
) -> SlippageModel:
    """Instantiate a slippage model by name.

    Args:
        name: Model name (fixed, percentage, volume, volatility, spread, random).
        **params: Constructor parameters for the model.

    Returns:
        A slippage model instance.

    Raises:
        ValueError: If the model name is unknown.
    """
    key = name.lower()
    if key not in SLIPPAGE_MODELS:
        raise ValueError(
            f"Unknown slippage model '{name}'. "
            f"Available: {sorted(SLIPPAGE_MODELS.keys())}"
        )
    return SLIPPAGE_MODELS[key](**params)
