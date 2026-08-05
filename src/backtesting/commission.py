"""Commission engine for the institutional backtesting engine.

Supports flat fees, percentage commissions, tiered schedules,
broker-specific schedules, exchange fees, taxes, and stamp duty
(Part 9.4).  Models are pluggable via the ``COMMISSION_MODELS``
registry.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommissionResult:
    """The computed charges for a single execution.

    Attributes:
        commission: Broker commission.
        exchange_fee: Exchange charge.
        tax: Tax charged on executed value.
        stamp_duty: Stamp duty charged on executed value.
        total: Sum of all components.
    """

    commission: float = 0.0
    exchange_fee: float = 0.0
    tax: float = 0.0
    stamp_duty: float = 0.0

    @property
    def total(self) -> float:
        """Return the combined charge."""
        return self.commission + self.exchange_fee + self.tax + self.stamp_duty

    def to_dict(self) -> dict[str, float]:
        """Return a JSON-serialisable dictionary."""
        return {
            "commission": round(self.commission, 2),
            "exchange_fee": round(self.exchange_fee, 2),
            "tax": round(self.tax, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "total": round(self.total, 2),
        }


class CommissionModel(ABC):
    """Abstract commission model.

    Subclasses implement :meth:`compute` returning the broker
    commission and exchange fee for an execution.  Tax and stamp
    duty are handled centrally by the engine via :class:`TaxPolicy`.
    """

    name: str = "base"

    @abstractmethod
    def compute(
        self,
        quantity: int,
        price: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        """Return ``(commission, exchange_fee)`` for an execution.

        Args:
            quantity: Number of shares executed.
            price: Execution price per share.
            context: Optional execution context.
        """
        raise NotImplementedError

    def charges(
        self,
        quantity: int,
        price: float,
        context: dict[str, Any] | None = None,
    ) -> CommissionResult:
        """Return a full :class:`CommissionResult` (commission + exchange fee)."""
        commission, exchange_fee = self.compute(quantity, price, context)
        return CommissionResult(commission=commission, exchange_fee=exchange_fee)


class FlatCommission(CommissionModel):
    """Fixed commission per execution regardless of value."""

    name = "flat"

    def __init__(self, fee: float = 10.0) -> None:
        """Initialise with a flat per-execution fee."""
        self.fee = max(0.0, float(fee))

    def compute(
        self,
        quantity: int,
        price: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        return self.fee, 0.0


class PercentageCommission(CommissionModel):
    """Commission proportional to executed value."""

    name = "percentage"

    def __init__(self, rate: float = 0.001, minimum: float = 0.0) -> None:
        """Initialise with a fractional rate and optional minimum."""
        self.rate = max(0.0, float(rate))
        self.minimum = max(0.0, float(minimum))

    def compute(
        self,
        quantity: int,
        price: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        value = quantity * price
        return max(self.minimum, value * self.rate), 0.0


class TieredCommission(CommissionModel):
    """Commission that steps down as executed value grows.

    Tiers are provided as sorted ``(min_value, rate)`` pairs; the
    rate of the highest tier whose minimum is exceeded is applied.
    """

    name = "tiered"

    def __init__(
        self,
        tiers: list[tuple[float, float]] | None = None,
    ) -> None:
        """Initialise with tier definitions.

        Args:
            tiers: Sorted ``(min_value, rate)`` pairs, e.g.
                ``[(0, 0.001), (1_000_000, 0.0008), (10_000_000, 0.0005)]``.
        """
        self.tiers = sorted(
            tiers or [(0.0, 0.001), (1_000_000.0, 0.0008), (10_000_000.0, 0.0005)]
        )

    def compute(
        self,
        quantity: int,
        price: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        value = quantity * price
        rate = self.tiers[0][1]
        for min_value, tier_rate in self.tiers:
            if value >= min_value:
                rate = tier_rate
        return value * rate, 0.0


class BrokerCommission(CommissionModel):
    """Nepal broker-style schedule: percentage + exchange fee + tax flags.

    Mirrors common NEPSE broker charges (broker 0.4%, exchange 0.01%,
    SEBON 0.015%, etc.) while remaining configurable.
    """

    name = "broker"

    def __init__(
        self,
        broker_rate: float = 0.004,
        exchange_rate: float = 0.0001,
        sebom_rate: float = 0.00015,
    ) -> None:
        """Initialise with NEPSE-style rates (fractions)."""
        self.broker_rate = max(0.0, float(broker_rate))
        self.exchange_rate = max(0.0, float(exchange_rate))
        self.sebom_rate = max(0.0, float(sebom_rate))

    def compute(
        self,
        quantity: int,
        price: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, float]:
        value = quantity * price
        broker = value * self.broker_rate
        exchange = value * self.exchange_rate + value * self.sebom_rate
        return broker, exchange


class TaxPolicy:
    """Centralised tax and stamp duty policy.

    Applies tax and stamp duty on top of a commission model's charges.
    """

    def __init__(
        self,
        tax_rate: float = 0.0,
        stamp_duty: float = 0.0,
        order_fee: float = 0.0,
    ) -> None:
        """Initialise tax rates.

        Args:
            tax_rate: Flat tax rate on executed value (fraction).
            stamp_duty: Stamp duty rate on executed value (fraction).
            order_fee: Flat per-order fee.
        """
        self.tax_rate = max(0.0, float(tax_rate))
        self.stamp_duty = max(0.0, float(stamp_duty))
        self.order_fee = max(0.0, float(order_fee))

    def apply(
        self,
        result: CommissionResult,
        quantity: int,
        price: float,
    ) -> CommissionResult:
        """Add tax, stamp duty, and order fee to a commission result."""
        value = quantity * price
        return CommissionResult(
            commission=result.commission + self.order_fee,
            exchange_fee=result.exchange_fee,
            tax=value * self.tax_rate,
            stamp_duty=value * self.stamp_duty,
        )


# Registry of available models by name.
COMMISSION_MODELS: dict[str, type[CommissionModel]] = {
    FlatCommission.name: FlatCommission,
    PercentageCommission.name: PercentageCommission,
    TieredCommission.name: TieredCommission,
    BrokerCommission.name: BrokerCommission,
}


def build_commission_model(
    name: str = "percentage",
    **params: Any,
) -> CommissionModel:
    """Instantiate a commission model by name.

    Args:
        name: Model name (flat, percentage, tiered, broker).
        **params: Constructor parameters.

    Returns:
        A commission model instance.

    Raises:
        ValueError: If the model name is unknown.
    """
    key = name.lower()
    if key not in COMMISSION_MODELS:
        raise ValueError(
            f"Unknown commission model '{name}'. "
            f"Available: {sorted(COMMISSION_MODELS.keys())}"
        )
    return COMMISSION_MODELS[key](**params)
