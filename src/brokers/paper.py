"""Paper broker — simulated trading without real money.

Executes orders against a supplied price feed, tracking cash,
positions, and order history in memory.
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


class PaperBroker(Broker):
    """In-memory paper trading broker.

    Usage::

        broker = PaperBroker(cash=1_000_000, price_feed=get_price)
        broker.connect()
        order = broker.place_order(
            Order(symbol="NABIL", side=OrderSide.BUY, quantity=100)
        )
    """

    name = "paper"

    def __init__(
        self,
        cash: float = 1_000_000.0,
        price_feed: PriceFeed | None = None,
        commission_rate: float = 0.0,
    ) -> None:
        """Initialise the paper broker.

        Args:
            cash: Starting cash balance.
            price_feed: Callable returning the current price for a
                symbol.  Defaults to a fixed price of 100.
            commission_rate: Per-trade commission fraction.
        """
        self._cash = float(cash)
        self._commission_rate = float(commission_rate)
        self._price_feed = price_feed or (lambda symbol: 100.0)
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._is_connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Mark the broker as connected."""
        self._is_connected = True
        logger.info("PaperBroker connected.")

    def disconnect(self) -> None:
        """Mark the broker as disconnected."""
        self._is_connected = False
        logger.info("PaperBroker disconnected.")

    @property
    def connected(self) -> bool:
        """Return True when connected."""
        return self._is_connected

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def place_order(self, order: Order) -> Order:
        """Execute an order immediately at the feed price.

        Args:
            order: Order to execute.

        Returns:
            The executed order.
        """
        if not self._is_connected:
            raise RuntimeError("PaperBroker is not connected.")

        order.order_id = self._new_id()
        price = self._safe_price(order.symbol)

        if order.quantity <= 0:
            order.status = OrderStatus.REJECTED
            self._orders[order.order_id] = order
            return order

        if order.side == OrderSide.BUY:
            cost = order.quantity * price
            commission = cost * self._commission_rate
            if cost + commission > self._cash:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                logger.warning("Paper order rejected: insufficient cash.")
                return order
            self._cash -= cost + commission
            self._add_position(order.symbol, order.quantity, price)
        else:
            position = self._positions.get(order.symbol)
            held = position.quantity if position else 0.0
            if held < order.quantity:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                logger.warning("Paper order rejected: insufficient shares.")
                return order
            proceeds = order.quantity * price
            commission = proceeds * self._commission_rate
            self._cash += proceeds - commission
            self._reduce_position(order.symbol, order.quantity, price)

        order.status = OrderStatus.EXECUTED
        order.filled_price = round(price, 4)
        self._orders[order.order_id] = order
        logger.info(
            "Paper order %s executed: %s %s x%.0f @ %.2f",
            order.order_id,
            order.side.value,
            order.symbol,
            order.quantity,
            price,
        )
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Args:
            order_id: Order ID.

        Returns:
            True when cancelled.
        """
        order = self._orders.get(order_id)
        if order is None or order.status not in (
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
        ):
            return False
        order.status = OrderStatus.CANCELLED
        return True

    def get_order(self, order_id: str) -> Order | None:
        """Return an order by ID."""
        return self._orders.get(order_id)

    def get_positions(self) -> list[Position]:
        """Return all open positions with current prices."""
        for position in self._positions.values():
            position.current_price = self._safe_price(position.symbol)
        return list(self._positions.values())

    def get_balance(self) -> AccountBalance:
        """Return the current account balance."""
        positions_value = sum(
            p.market_value() for p in self._positions.values()
        )
        equity = self._cash + positions_value
        return AccountBalance(
            cash=round(self._cash, 2),
            equity=round(equity, 2),
            buying_power=round(self._cash, 2),
            currency="NPR",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        """Generate a unique order id."""
        return uuid.uuid4().hex[:16].upper()

    def _safe_price(self, symbol: str) -> float:
        """Fetch the price feed value safely."""
        try:
            return float(self._price_feed(symbol))
        except Exception:
            return 100.0

    def _add_position(self, symbol: str, quantity: float, price: float) -> None:
        """Add shares to a position, updating the average price."""
        position = self._positions.get(symbol)
        if position is None:
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                average_price=price,
                current_price=price,
            )
            return
        total_cost = position.average_price * position.quantity + price * quantity
        position.quantity += quantity
        position.average_price = (
            total_cost / position.quantity if position.quantity else 0.0
        )

    def _reduce_position(self, symbol: str, quantity: float, price: float) -> None:
        """Remove shares from a position, dropping it at zero."""
        position = self._positions.get(symbol)
        if position is None:
            return
        position.quantity -= quantity
        if position.quantity <= 1e-9:
            self._positions.pop(symbol, None)
