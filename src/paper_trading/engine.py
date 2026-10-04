"""Paper trading engine — order execution, position management, P&L tracking.

Supports market orders, limit orders, stop-loss orders, and take-profit orders
with partial fills and a pending order queue.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.paper_trading.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    OpenPosition,
    PaperTrade,
    PaperTradingSummary,
)

logger = logging.getLogger(__name__)

INITIAL_BALANCE = 1_000_000.0
COMMISSION_RATE = 0.001  # 0.1%


class PaperTradingEngine:
    """In-memory paper trading engine.

    Orders are queued and executed on ``process_orders()`` with the
    current market price.  Supports partial fills for limit orders,
    stop-loss triggers when price crosses thresholds, and take-profit
    auto-exits.
    """

    def __init__(self, initial_balance: float = INITIAL_BALANCE) -> None:
        self._balance = initial_balance
        self._positions: dict[str, OpenPosition] = {}
        self._orders: list[Order] = []
        self._trades: list[PaperTrade] = []
        self._order_counter = 0

    # ── Properties ───────────────────────────────────────────────

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def positions(self) -> list[OpenPosition]:
        return list(self._positions.values())

    @property
    def orders(self) -> list[Order]:
        return list(self._orders)

    @property
    def trades(self) -> list[PaperTrade]:
        return list(self._trades)

    @property
    def pending_orders(self) -> list[Order]:
        return [o for o in self._orders if o.is_open]

    # ── Balance management ───────────────────────────────────────

    def deposit(self, amount: float) -> float:
        """Add funds.  Returns new balance."""
        self._balance += amount
        return self._balance

    def withdraw(self, amount: float) -> bool:
        """Withdraw funds.  Returns False if insufficient balance."""
        if amount > self._balance:
            return False
        self._balance -= amount
        return True

    # ── Order placement ──────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        stop_price: float = 0.0,
    ) -> Order:
        """Place a new order.  Returns the created Order."""
        self._order_counter += 1
        order = Order(
            id=f"ORD-{self._order_counter:06d}",
            symbol=symbol.upper(),
            side=side,
            order_type=order_type,
            status=OrderStatus.PENDING,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            created_at=datetime.now(),
        )
        self._orders.append(order)
        logger.info("[PaperTrading] Placed %s: %s %d %s @ %.2f", order.id, side.value, quantity, symbol, price or 0)
        return order

    def buy_market(self, symbol: str, quantity: int) -> Order:
        """Place a market buy order."""
        return self.place_order(symbol, OrderSide.BUY, quantity, OrderType.MARKET)

    def sell_market(self, symbol: str, quantity: int) -> Order:
        """Place a market sell order."""
        return self.place_order(symbol, OrderSide.SELL, quantity, OrderType.MARKET)

    def buy_limit(self, symbol: str, quantity: int, limit_price: float) -> Order:
        """Place a limit buy order."""
        return self.place_order(symbol, OrderSide.BUY, quantity, OrderType.LIMIT, price=limit_price)

    def sell_limit(self, symbol: str, quantity: int, limit_price: float) -> Order:
        """Place a limit sell order."""
        return self.place_order(symbol, OrderSide.SELL, quantity, OrderType.LIMIT, price=limit_price)

    def stop_loss(self, symbol: str, quantity: int, stop_price: float) -> Order:
        """Place a stop-loss sell order."""
        return self.place_order(symbol, OrderSide.SELL, quantity, OrderType.STOP_LOSS, stop_price=stop_price)

    def take_profit(self, symbol: str, quantity: int, target_price: float) -> Order:
        """Place a take-profit sell order."""
        return self.place_order(symbol, OrderSide.SELL, quantity, OrderType.TAKE_PROFIT, stop_price=target_price)

    # ── Order execution ──────────────────────────────────────────

    def process_orders(self, live_prices: dict[str, float]) -> list[Order]:
        """Execute all executable orders against *live_prices*.

        Returns the list of orders that were filled or updated.
        """
        filled: list[Order] = []
        for order in list(self._orders):
            if not order.is_open:
                continue

            price = live_prices.get(order.symbol)
            if price is None or price <= 0:
                continue

            if self._should_execute(order, price):
                self._fill_order(order, price)
                filled.append(order)

        return filled

    def _should_execute(self, order: Order, current_price: float) -> bool:
        """Check if an order should execute at *current_price*."""
        if order.order_type == OrderType.MARKET:
            return True
        if order.order_type == OrderType.LIMIT:
            if order.side == OrderSide.BUY:
                return current_price <= order.price
            return current_price >= order.price
        if order.order_type == OrderType.STOP_LOSS:
            return current_price <= order.stop_price
        if order.order_type == OrderType.TAKE_PROFIT:
            return current_price >= order.stop_price
        return False

    def _fill_order(self, order: Order, fill_price: float) -> None:
        """Fill an order at *fill_price*."""
        symbol = order.symbol
        quantity = order.quantity
        total = fill_price * quantity
        commission = total * COMMISSION_RATE

        if order.side == OrderSide.BUY:
            cost = total + commission
            if cost > self._balance:
                order.status = OrderStatus.REJECTED
                order.notes = "Insufficient balance"
                logger.warning("[PaperTrading] %s REJECTED: insufficient balance", order.id)
                return

            self._balance -= cost

            # Add to / update position
            pos = self._positions.get(symbol)
            if pos:
                new_qty = pos.quantity + quantity
                new_invested = pos.invested + total
                pos.average_price = new_invested / new_qty
                pos.quantity = new_qty
                pos.invested = new_invested
            else:
                self._positions[symbol] = OpenPosition(
                    symbol=symbol,
                    quantity=quantity,
                    average_price=fill_price,
                    invested=total,
                    current_price=fill_price,
                    current_value=total,
                )

            order.status = OrderStatus.EXECUTED
            order.filled_quantity = quantity
            order.price = fill_price
            order.total = total
            order.commission = commission
            order.filled_at = datetime.now()

            self._trades.append(PaperTrade(
                id=order.id,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                entry_price=fill_price,
                commission=commission,
                entry_date=order.filled_at,
                order_id=order.id,
            ))

        else:  # SELL
            pos = self._positions.get(symbol)
            if not pos or pos.quantity < quantity:
                order.status = OrderStatus.REJECTED
                order.notes = f"Insufficient shares: have {pos.quantity if pos else 0}, need {quantity}"
                logger.warning("[PaperTrading] %s REJECTED: insufficient shares", order.id)
                return

            proceeds = total - commission
            self._balance += proceeds

            cost_basis = pos.average_price * quantity
            trade_pnl = proceeds - cost_basis

            # Update position
            pos.quantity -= quantity
            pos.invested = pos.average_price * pos.quantity
            pos.realized_pnl += trade_pnl

            if pos.quantity <= 0:
                del self._positions[symbol]

            order.status = OrderStatus.EXECUTED
            order.filled_quantity = quantity
            order.price = fill_price
            order.total = total
            order.commission = commission
            order.pnl = trade_pnl
            order.filled_at = datetime.now()

            pnl_pct = (trade_pnl / cost_basis * 100) if cost_basis > 0 else 0
            self._trades.append(PaperTrade(
                id=order.id,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                entry_price=pos.average_price,
                exit_price=fill_price,
                pnl=trade_pnl,
                pnl_pct=pnl_pct,
                commission=commission,
                entry_date=order.created_at,
                exit_date=order.filled_at,
                order_id=order.id,
            ))

        logger.info(
            "[PaperTrading] %s FILLED: %d %s @ %.2f (P&L: %.2f)",
            order.id, quantity, symbol, fill_price, order.pnl,
        )

    # ── Order management ─────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.  Returns True on success."""
        for order in self._orders:
            if order.id == order_id and order.is_open:
                order.status = OrderStatus.CANCELLED
                order.notes = "Cancelled by user"
                logger.info("[PaperTrading] %s CANCELLED", order_id)
                return True
        return False

    def get_order(self, order_id: str) -> Order | None:
        """Find an order by ID."""
        for order in self._orders:
            if order.id == order_id:
                return order
        return None

    # ── Position valuation ───────────────────────────────────────

    def update_prices(self, live_prices: dict[str, float]) -> None:
        """Update all open positions with current market prices."""
        for symbol, price in live_prices.items():
            pos = self._positions.get(symbol)
            if pos and price > 0:
                pos.current_price = price
                pos.current_value = price * pos.quantity
                pos.unrealized_pnl = pos.current_value - pos.invested
                pos.unrealized_pnl_pct = (pos.unrealized_pnl / pos.invested * 100) if pos.invested > 0 else 0.0

    # ── Summary ──────────────────────────────────────────────────

    def get_summary(self) -> PaperTradingSummary:
        """Return a summary of the paper trading account."""
        invested = sum(p.invested for p in self._positions.values())
        current_value = sum(p.current_value for p in self._positions.values())
        unrealized_pnl = sum(p.unrealized_pnl for p in self._positions.values())
        realized_pnl = sum(p.realized_pnl for p in self._positions.values())
        total_pnl = unrealized_pnl + realized_pnl
        total_pnl_pct = (total_pnl / (invested or 1)) * 100

        win_count = sum(1 for t in self._trades if t.pnl > 0)
        loss_count = sum(1 for t in self._trades if t.pnl < 0)
        total_trades = len([t for t in self._trades if t.side == OrderSide.SELL])
        win_rate = (win_count / max(total_trades, 1)) * 100

        recent = sorted(self._trades, key=lambda t: t.exit_date or t.entry_date, reverse=True)[:20]

        return PaperTradingSummary(
            balance=self._balance,
            invested=invested,
            current_value=current_value + self._balance,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            total_trades=total_trades,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            open_positions_count=len(self._positions),
            open_positions=list(self._positions.values()),
            recent_trades=recent,
            pending_orders=self.pending_orders,
        )
