"""Order management for the institutional backtesting engine.

Implements advanced order types (market, limit, stop, stop-limit,
trailing stop, bracket, OCO, IOC, FOK) and a strict lifecycle state
machine (Part 9.2).  The :class:`OrderManager` owns all orders,
enforces transitions, and exposes working/expired order queries.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from src.backtesting.models import (
    EventType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

logger = logging.getLogger(__name__)

# Valid lifecycle transitions.
_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED},
    OrderStatus.SUBMITTED: {OrderStatus.ACCEPTED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED},
    OrderStatus.ACCEPTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}


class OrderManager:
    """Owns orders and enforces the lifecycle state machine.

    Usage::

        manager = OrderManager()
        order = manager.create_order(symbol="NABIL", side=OrderSide.BUY,
                                     order_type=OrderType.LIMIT, quantity=100,
                                     limit_price=500.0)
        manager.submit(order)
        manager.accept(order)
        manager.cancel(order)
    """

    def __init__(self) -> None:
        """Initialise an empty order manager."""
        self._orders: dict[str, Order] = {}
        self._seq = 0

    # ── Factories ───────────────────────────────────────────────

    def _next_id(self, prefix: str) -> str:
        """Return the next unique order id."""
        self._seq += 1
        return f"{prefix}{self._seq}"

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        expires_at: datetime | None = None,
        trailing_offset: float | None = None,
        trailing_pct: float | None = None,
        oco_group: str | None = None,
        bracket_parent: str | None = None,
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Order:
        """Create a new order in the CREATED state.

        Args:
            symbol: Ticker symbol.
            side: BUY or SELL.
            quantity: Quantity (positive integer).
            order_type: Order type.
            limit_price: Limit price for LIMIT / STOP_LIMIT.
            stop_price: Stop trigger for STOP / STOP_LIMIT / TRAILING_STOP.
            time_in_force: TIF policy.
            expires_at: Expiry for GTD orders.
            trailing_offset: Absolute trail distance.
            trailing_pct: Fractional trail distance.
            oco_group: OCO group id for one-cancels-other families.
            bracket_parent: Parent order id for bracket orders.
            note: Optional note.
            metadata: Extra payload.

        Returns:
            The created order.

        Raises:
            ValueError: If quantity is not positive or prices are invalid.
        """
        if quantity <= 0:
            raise ValueError("Order quantity must be positive.")
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
            raise ValueError(f"{order_type.value} orders require a limit price.")
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP) and stop_price is None:
            raise ValueError(f"{order_type.value} orders require a stop price.")

        order = Order(
            order_id=self._next_id("ORD-"),
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            status=OrderStatus.CREATED,
            time_in_force=time_in_force,
            limit_price=limit_price,
            stop_price=stop_price,
            trailing_offset=trailing_offset,
            trailing_pct=trailing_pct,
            expires_at=expires_at,
            oco_group=oco_group,
            bracket_parent=bracket_parent,
            note=note,
            metadata=metadata or {},
        )
        self._orders[order.order_id] = order
        logger.debug("Created %s", order.order_id)
        return order

    # Convenience factories
    def market(self, symbol: str, side: OrderSide, quantity: int, **kwargs: Any) -> Order:
        """Create a MARKET order."""
        return self.create_order(symbol, side, quantity, OrderType.MARKET, **kwargs)

    def limit(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        **kwargs: Any,
    ) -> Order:
        """Create a LIMIT order."""
        return self.create_order(symbol, side, quantity, OrderType.LIMIT, limit_price=price, **kwargs)

    def stop(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        stop_price: float,
        **kwargs: Any,
    ) -> Order:
        """Create a STOP order."""
        return self.create_order(symbol, side, quantity, OrderType.STOP, stop_price=stop_price, **kwargs)

    def stop_limit(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        stop_price: float,
        limit_price: float,
        **kwargs: Any,
    ) -> Order:
        """Create a STOP_LIMIT order."""
        return self.create_order(
            symbol, side, quantity, OrderType.STOP_LIMIT,
            stop_price=stop_price, limit_price=limit_price, **kwargs,
        )

    def trailing_stop(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        trail_price: float,
        offset: float | None = None,
        pct: float | None = None,
        **kwargs: Any,
    ) -> Order:
        """Create a TRAILING_STOP order anchored at *trail_price*."""
        if offset is None and pct is None:
            raise ValueError("Trailing stop requires either an offset or a percentage.")
        return self.create_order(
            symbol, side, quantity, OrderType.TRAILING_STOP,
            stop_price=trail_price, trailing_offset=offset, trailing_pct=pct, **kwargs,
        )

    def bracket(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        **kwargs: Any,
    ) -> list[Order]:
        """Create a BRACKET family: entry + take-profit + stop-loss.

        Args:
            symbol: Ticker symbol.
            side: Entry side (BUY or SELL).
            quantity: Quantity.
            entry_price: Reference entry price (market order).
            take_profit: Profit target price.
            stop_loss: Stop-loss price.
            **kwargs: Extra keyword arguments.

        Returns:
            ``[entry, take_profit, stop_loss]`` with the exit legs
            linked to the entry via ``bracket_parent`` and sharing an
            OCO group so only one exit may fill.
        """
        oco_group = uuid.uuid4().hex[:8]
        entry = self.market(symbol, side, quantity, note="bracket entry", **kwargs)
        oco_kwargs = {"oco_group": oco_group, "bracket_parent": entry.order_id}
        if side == OrderSide.BUY:
            tp = self.limit(symbol, OrderSide.SELL, quantity, take_profit, note="bracket take-profit", **oco_kwargs)
            sl = self.stop(symbol, OrderSide.SELL, quantity, stop_loss, note="bracket stop-loss", **oco_kwargs)
        else:
            tp = self.limit(symbol, OrderSide.BUY, quantity, take_profit, note="bracket take-profit", **oco_kwargs)
            sl = self.stop(symbol, OrderSide.BUY, quantity, stop_loss, note="bracket stop-loss", **oco_kwargs)
        return [entry, tp, sl]

    def oco(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price_a: float,
        price_b: float,
        type_a: OrderType = OrderType.LIMIT,
        type_b: OrderType = OrderType.LIMIT,
        **kwargs: Any,
    ) -> list[Order]:
        """Create an OCO pair — when one fills, the other is cancelled.

        Args:
            symbol: Ticker symbol.
            side: Side for both legs.
            quantity: Quantity for both legs.
            price_a: Price for leg A.
            price_b: Price for leg B.
            type_a: Order type for leg A.
            type_b: Order type for leg B.
            **kwargs: Extra keyword arguments.

        Returns:
            ``[leg_a, leg_b]`` sharing an OCO group.
        """
        group = uuid.uuid4().hex[:8]
        a = self.create_order(symbol, side, quantity, type_a, limit_price=price_a, oco_group=group, **kwargs)
        b = self.create_order(symbol, side, quantity, type_b, limit_price=price_b, oco_group=group, **kwargs)
        return [a, b]

    def ioc(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        """Create an IOC order (fill immediately or cancel remainder)."""
        return self.create_order(
            symbol, side, quantity,
            OrderType.LIMIT if limit_price is not None else OrderType.MARKET,
            limit_price=limit_price, time_in_force=TimeInForce.IOC, **kwargs,
        )

    def fok(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        """Create an FOK order (fill entire quantity or kill)."""
        return self.create_order(
            symbol, side, quantity,
            OrderType.LIMIT if limit_price is not None else OrderType.MARKET,
            limit_price=limit_price, time_in_force=TimeInForce.FOK, **kwargs,
        )

    def gtd(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        days: int,
        **kwargs: Any,
    ) -> Order:
        """Create a GTD (good-till-date) order expiring in *days*."""
        return self.create_order(
            symbol, side, quantity, order_type,
            time_in_force=TimeInForce.GTD,
            expires_at=datetime.now() + timedelta(days=days),
            **kwargs,
        )

    # ── Lifecycle transitions ───────────────────────────────────

    def _transition(self, order: Order, new_status: OrderStatus) -> None:
        """Validate and apply a status transition.

        Args:
            order: Order to mutate.
            new_status: Target status.

        Raises:
            ValueError: If the transition is not allowed.
        """
        allowed = _TRANSITIONS.get(order.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal order transition {order.status.value} -> {new_status.value} "
                f"for {order.order_id}."
            )
        order.status = new_status
        logger.debug("%s -> %s (%s)", order.order_id, new_status.value, order.status.value)

    def submit(self, order: Order) -> None:
        """Move CREATED -> SUBMITTED."""
        self._transition(order, OrderStatus.SUBMITTED)

    def accept(self, order: Order) -> None:
        """Move SUBMITTED -> ACCEPTED."""
        self._transition(order, OrderStatus.ACCEPTED)

    def reject(self, order: Order, reason: str = "") -> None:
        """Move to REJECTED, optionally recording a reason."""
        self._transition(order, OrderStatus.REJECTED)
        order.note = reason or order.note

    def cancel(self, order: Order) -> None:
        """Cancel an open order (to CANCELLED)."""
        if order.status not in (OrderStatus.FILLED,):
            self._transition(order, OrderStatus.CANCELLED)

    def expire(self, order: Order) -> None:
        """Expire an open order (to EXPIRED)."""
        if order.status not in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self._transition(order, OrderStatus.EXPIRED)

    def mark_filled(self, order: Order) -> None:
        """Force an order to FILLED (used by the fill engine hook)."""
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
            return
        order.status = OrderStatus.FILLED

    # ── Queries ─────────────────────────────────────────────────

    def get(self, order_id: str) -> Order | None:
        """Return an order by id, or None."""
        return self._orders.get(order_id)

    def all_orders(self) -> list[Order]:
        """Return all orders in creation order."""
        return list(self._orders.values())

    def working_orders(self, symbol: str | None = None) -> list[Order]:
        """Return open (fillable) orders, optionally filtered by symbol."""
        return [
            o for o in self._orders.values()
            if o.is_open and (symbol is None or o.symbol == symbol)
        ]

    def orders_by_status(self, status: OrderStatus) -> list[Order]:
        """Return orders in a given status."""
        return [o for o in self._orders.values() if o.status == status]

    def cancel_oco_siblings(self, order: Order) -> list[Order]:
        """Cancel all working orders sharing the OCO group of *order*.

        Args:
            order: The order that filled.

        Returns:
            The list of cancelled sibling orders.
        """
        if not order.oco_group:
            return []
        cancelled: list[Order] = []
        for sibling in self._orders.values():
            if (
                sibling.oco_group == order.oco_group
                and sibling.order_id != order.order_id
                and sibling.is_open
            ):
                self.cancel(sibling)
                cancelled.append(sibling)
        return cancelled

    def expire_gtd_orders(self, now: datetime) -> list[Order]:
        """Expire GTD orders whose expiry has passed.

        Args:
            now: Current time.

        Returns:
            List of newly expired orders.
        """
        expired: list[Order] = []
        for order in self._orders.values():
            if (
                order.is_open
                and order.time_in_force == TimeInForce.GTD
                and order.expires_at is not None
                and order.expires_at <= now
            ):
                self.expire(order)
                expired.append(order)
        return expired

    def update_trailing_stops(
        self,
        order: Order,
        bar_price: float,
    ) -> None:
        """Ratchet a TRAILING_STOP order's stop toward the market.

        Args:
            order: The trailing-stop order (in place).
            bar_price: Current market price.
        """
        if order.order_type != OrderType.TRAILING_STOP or not order.is_open:
            return
        if order.side == OrderSide.SELL:
            # SELL stop (protects a long position): trails UP as price
            # rises, keeping a fixed distance below the market.
            if order.trailing_offset is not None:
                new_stop = bar_price - order.trailing_offset
            elif order.trailing_pct is not None:
                new_stop = bar_price * (1 - order.trailing_pct)
            else:
                return
            if order.stop_price is None or new_stop > order.stop_price:
                order.stop_price = new_stop
        else:
            # BUY stop (protects a short position): trails DOWN as
            # price falls, keeping a fixed distance above the market.
            if order.trailing_offset is not None:
                new_stop = bar_price + order.trailing_offset
            elif order.trailing_pct is not None:
                new_stop = bar_price * (1 + order.trailing_pct)
            else:
                return
            if order.stop_price is None or new_stop < order.stop_price:
                order.stop_price = new_stop

    def event_for(self, order: Order) -> EventType:
        """Map an order's status to the corresponding event type."""
        mapping = {
            OrderStatus.CREATED: EventType.ORDER_CREATED,
            OrderStatus.SUBMITTED: EventType.ORDER_SUBMITTED,
            OrderStatus.ACCEPTED: EventType.ORDER_ACCEPTED,
            OrderStatus.REJECTED: EventType.ORDER_REJECTED,
            OrderStatus.CANCELLED: EventType.ORDER_CANCELLED,
            OrderStatus.EXPIRED: EventType.ORDER_EXPIRED,
        }
        return mapping.get(order.status, EventType.ORDER_SUBMITTED)

    def clear(self) -> None:
        """Remove all orders."""
        self._orders.clear()
        self._seq = 0
