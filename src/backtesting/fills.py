"""Fill engine for the institutional backtesting engine.

Determines whether an order executes against a given bar, computes
the execution price (after slippage), and applies commission,
taxes, and stamp duty.  Supports partial fills for volume-limited
executions.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.backtesting.commission import (
    CommissionModel,
    CommissionResult,
    TaxPolicy,
    build_commission_model,
)
from src.backtesting.models import (
    Bar,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from src.backtesting.slippage import SlippageModel, build_slippage_model

logger = logging.getLogger(__name__)


class FillEngine:
    """Executes orders against bars.

    The engine decides *whether* a bar fills an order (based on
    order type and prices), computes the execution price with the
    configured slippage model, and charges commission/tax/stamp duty.

    Usage::

        engine = FillEngine(slippage="fixed", commission="percentage")
        fill = engine.try_fill(order, bar, participation=0.25)
    """

    def __init__(
        self,
        slippage: SlippageModel | str = "fixed",
        commission: CommissionModel | str = "percentage",
        tax_policy: TaxPolicy | None = None,
        slippage_params: dict[str, Any] | None = None,
        commission_params: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the fill engine.

        Args:
            slippage: Slippage model instance or registered name.
            commission: Commission model instance or registered name.
            tax_policy: Optional tax policy; defaults to zero rates.
            slippage_params: Constructor params when slippage is a name.
            commission_params: Constructor params when commission is a name.
        """
        self.slippage: SlippageModel = (
            slippage
            if isinstance(slippage, SlippageModel)
            else build_slippage_model(slippage, **(slippage_params or {}))
        )
        self.commission: CommissionModel = (
            commission
            if isinstance(commission, CommissionModel)
            else build_commission_model(commission, **(commission_params or {}))
        )
        self.tax_policy = tax_policy or TaxPolicy()

    # ── Fill decision ───────────────────────────────────────────

    def should_fill(self, order: Order, bar: Bar, base_price: float) -> bool:
        """Return whether the bar triggers execution of *order*.

        Args:
            order: The order under consideration.
            bar: Current market bar.
            base_price: The reference price (typically bar.open).

        Returns:
            ``True`` when the order should execute this bar.
        """
        if order.quantity <= 0:
            return False
        if order.remaining_quantity <= 0:
            return False

        otype = order.order_type

        if otype == OrderType.MARKET:
            return True

        if otype in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            limit = order.limit_price
            if limit is None:
                return True
            if order.side.value == "BUY":
                return base_price <= limit or bar.low <= limit
            return base_price >= limit or bar.high >= limit

        if otype in (OrderType.STOP, OrderType.TRAILING_STOP):
            stop = order.stop_price
            if stop is None:
                return True
            if order.side.value == "BUY":
                return base_price >= stop or bar.high >= stop
            return base_price <= stop or bar.low <= stop

        if otype == OrderType.IOC:
            return True

        if otype == OrderType.FOK:
            return True

        return True

    def execution_price(
        self,
        order: Order,
        bar: Bar,
        base_price: float,
        context: dict[str, Any] | None = None,
    ) -> float:
        """Compute the execution price including slippage.

        For limit orders the fill never improves beyond the limit in
        the adverse direction; for stop orders the fill occurs at the
        stop level (with slippage).  Falls back to the base price when
        a modelled price is invalid.
        """
        otype = order.order_type

        if otype in (OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP):
            ref = order.stop_price if order.stop_price is not None else base_price
        elif otype == OrderType.LIMIT:
            ref = order.limit_price if order.limit_price is not None else base_price
        else:
            ref = base_price

        if ref is None or ref <= 0:
            ref = base_price

        price = self.slippage.adjust(order, bar, float(ref), context)

        # Enforce limit constraints on the final price.
        if otype in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price is not None:
            if order.side.value == "BUY":
                price = min(price, float(order.limit_price))
            else:
                price = max(price, float(order.limit_price))
        if price <= 0:
            price = float(ref)
        return price

    # ── Execution ───────────────────────────────────────────────

    def charge(
        self,
        quantity: int,
        price: float,
    ) -> CommissionResult:
        """Compute charges for a fill (commission + fees + tax + stamp)."""
        result = self.commission.charges(quantity, price)
        return self.tax_policy.apply(result, quantity, price)

    def try_fill(
        self,
        order: Order,
        bar: Bar,
        base_price: float | None = None,
        available_quantity: int | None = None,
    ) -> Fill | None:
        """Attempt to fill *order* against *bar*.

        Args:
            order: The order (mutated in place on fill).
            bar: Current market bar.
            base_price: Reference price; defaults to ``bar.open``.
            available_quantity: Cap on executable quantity (for
                volume-limited fills).  ``None`` means unlimited.

        Returns:
            A :class:`Fill` on success, or ``None`` if the bar does
            not trigger the order.
        """
        base_price = base_price if base_price is not None else bar.open
        if not self.should_fill(order, bar, base_price):
            return None

        # FOK requires the full quantity; otherwise abort.
        if order.time_in_force == TimeInForce.FOK and available_quantity is not None:
            if available_quantity < order.remaining_quantity:
                return None

        qty = order.remaining_quantity
        if available_quantity is not None:
            qty = min(qty, max(available_quantity, 0))

        if qty <= 0:
            return None

        price = self.execution_price(order, bar, base_price)
        charges = self.charge(qty, price)

        slip_amount = price - base_price if order.side.value == "BUY" else base_price - price

        fill = Fill(
            fill_id=uuid.uuid4().hex[:12],
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=qty,
            price=round(price, 6),
            commission=charges.total,  # full charge incl. tax & stamp duty
            slippage_cost=round(slip_amount * qty, 2),
            timestamp=bar.timestamp,
        )

        # Update order state in place.
        order.filled_quantity += qty
        total_value = order.filled_value
        prev_qty = order.filled_quantity - qty
        if order.filled_quantity > 0:
            order.average_fill_price = (
                order.average_fill_price * prev_qty + price * qty
            ) / order.filled_quantity
        order.filled_value = total_value + (price * qty if order.side.value == "BUY" else -price * qty)
        order.commission += charges.total
        order.slippage += fill.slippage_cost

        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        elif order.status != OrderStatus.PARTIALLY_FILLED:
            order.status = OrderStatus.PARTIALLY_FILLED

        logger.debug(
            "Fill %s: %d x %s @ %.2f (status=%s)",
            fill.fill_id,
            qty,
            order.symbol,
            price,
            order.status.value,
        )
        return fill
