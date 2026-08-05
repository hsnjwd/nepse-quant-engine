"""Mock broker — deterministic fake trading for tests and demos.

Executes orders against a configurable price function with full order
book tracking, but with no execution realism.  Useful for unit tests
and the broker manager UI.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from src.brokers.base import (
    AccountBalance,
    Broker,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)
from src.logging.logger import logger

PriceFeed = Callable[[str], float]


class MockBroker(Broker):
    """Deterministic mock broker.

    Usage::

        broker = MockBroker(price_feed=lambda s: 500.0)
        broker.connect()
        order = broker.place_order(
            Order(symbol="NABIL", side=OrderSide.BUY, quantity=10)
        )
    """

    name = "mock"

    def __init__(
        self,
        cash: float = 500_000.0,
        price_feed: PriceFeed | None = None,
        fill_delay: float = 0.0,
    ) -> None:
        """Initialise the mock broker.

        Args:
            cash: Starting cash balance.
            price_feed: Callable returning prices.  Defaults to a
                deterministic 500 + symbol hash price.
            fill_delay: Simulated fill delay in seconds (unused).
        """
        self._cash = float(cash)
        self._price_feed = price_feed or self._default_price
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._is_connected = False

    @staticmethod
    def _default_price(symbol: str) -> float:
        """Deterministic pseudo price from the symbol."""
        return 500.0 + (sum(ord(c) for c in symbol) % 100)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Mark the broker as connected."""
        self._is_connected = True
        logger.info("MockBroker connected.")

    def disconnect(self) -> None:
        """Mark the broker as disconnected."""
        self._is_connected = False

    @property
    def connected(self) -> bool:
        """Return True when connected."""
        return self._is_connected

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_order(self, order: Order) -> Order:
        """Execute an order at the current mock price.

        Args:
            order: Order to execute.

        Returns:
            The executed order.
        """
        if not self._is_connected:
            raise RuntimeError("MockBroker is not connected.")

        order.order_id = self._new_id()
        price = self._safe_price(order.symbol)

        if order.quantity <= 0:
            order.status = OrderStatus.REJECTED
            self._orders[order.order_id] = order
            return order

        if order.side == OrderSide.BUY:
            cost = order.quantity * price
            if cost > self._cash:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                return order
            self._cash -= cost
            self._add_position(order.symbol, order.quantity, price)
        else:
            position = self._positions.get(order.symbol)
            held = position.quantity if position else 0.0
            if held < order.quantity:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                return order
            self._cash += order.quantity * price
            self._reduce_position(order.symbol, order.quantity)

        order.status = OrderStatus.EXECUTED
        order.filled_price = round(price, 4)
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        order = self._orders.get(order_id)
        if order is None or order.status != OrderStatus.PENDING:
            return False
        order.status = OrderStatus.CANCELLED
        return True

    def get_order(self, order_id: str) -> Order | None:
        """Return an order by ID."""
        return self._orders.get(order_id)

    def get_positions(self) -> list[Position]:
        """Return open positions."""
        for position in self._positions.values():
            position.current_price = self._safe_price(position.symbol)
        return list(self._positions.values())

    def get_balance(self) -> AccountBalance:
        """Return the account balance."""
        positions_value = sum(
            p.market_value() for p in self._positions.values()
        )
        return AccountBalance(
            cash=round(self._cash, 2),
            equity=round(self._cash + positions_value, 2),
            buying_power=round(self._cash, 2),
            currency="NPR",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        """Generate a unique order id."""
        return "MOCK-" + uuid.uuid4().hex[:12].upper()

    def _safe_price(self, symbol: str) -> float:
        """Fetch the mock price safely."""
        try:
            return float(self._price_feed(symbol))
        except Exception:
            return 500.0

    def _add_position(self, symbol: str, quantity: float, price: float) -> None:
        """Add shares to a position."""
        position = self._positions.get(symbol)
        if position is None:
            self._positions[symbol] = Position(
                symbol=symbol, quantity=quantity, average_price=price, current_price=price
            )
            return
        total_cost = position.average_price * position.quantity + price * quantity
        position.quantity += quantity
        position.average_price = (
            total_cost / position.quantity if position.quantity else 0.0
        )

    def _reduce_position(self, symbol: str, quantity: float) -> None:
        """Remove shares from a position."""
        position = self._positions.get(symbol)
        if position is None:
            return
        position.quantity -= quantity
        if position.quantity <= 1e-9:
            self._positions.pop(symbol, None)
