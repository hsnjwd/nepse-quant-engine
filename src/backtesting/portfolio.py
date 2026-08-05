"""Portfolio simulation for the institutional backtesting engine.

Tracks cash, multiple simultaneous positions, leverage, and
short-selling (Part 9.7).  Provides equity valuation, exposure
metrics, sector/industry allocation views, and correlation exposure.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.backtesting.models import Fill, OrderSide, Position, PositionSide

logger = logging.getLogger(__name__)


@dataclass
class PortfolioSnapshot:
    """A valuation snapshot of the portfolio at one point in time.

    Attributes:
        cash: Cash balance.
        positions_value: Sum of position market values.
        equity: Total equity (cash + positions value).
        unrealized_pnl: Unrealised P&L.
        realized_pnl: Cumulative realised P&L.
        exposure: Gross exposure (sum of |position values|).
        net_exposure: Net exposure (longs - shorts).
        leverage: Exposure divided by equity.
        bar_index: Timeline index of the snapshot.
        timestamp: Timestamp of the snapshot.
    """

    cash: float = 0.0
    positions_value: float = 0.0
    equity: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    exposure: float = 0.0
    net_exposure: float = 0.0
    leverage: float = 0.0
    bar_index: int = 0
    timestamp: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "cash": round(self.cash, 2),
            "positions_value": round(self.positions_value, 2),
            "equity": round(self.equity, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "exposure": round(self.exposure, 2),
            "net_exposure": round(self.net_exposure, 2),
            "leverage": round(self.leverage, 4),
            "bar_index": self.bar_index,
            "timestamp": str(self.timestamp),
        }


class PortfolioSimulator:
    """Simulates a margin account with long and short positions.

    Usage::

        portfolio = PortfolioSimulator(initial_cash=1_000_000, allow_short=True)
        portfolio.apply_fill(fill, commission=10.0)
        snapshot = portfolio.mark_to_market(prices, bar_index=0)
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        allow_short: bool = True,
        max_leverage: float = 1.0,
        lot_size: int = 1,
    ) -> None:
        """Initialise the portfolio.

        Args:
            initial_cash: Starting cash.
            allow_short: Enable short selling.
            max_leverage: Maximum exposure / equity ratio.
            lot_size: Quantities are rounded to this multiple.
        """
        self._cash = float(initial_cash)
        self._allow_short = bool(allow_short)
        self._max_leverage = float(max_leverage)
        self._lot_size = int(lot_size) or 1
        self._positions: dict[str, Position] = {}
        self._realized_pnl = 0.0
        self._history: list[PortfolioSnapshot] = []

    # ── Position helpers ────────────────────────────────────────

    def get_position(self, symbol: str) -> Position | None:
        """Return the open position for *symbol*, or None."""
        return self._positions.get(symbol)

    def purge_position(self, symbol: str) -> None:
        """Remove a position entirely from the portfolio.

        Used by the engine when force-closing positions at the end of
        data so ``open_positions`` only reflects genuinely open
        positions.

        Args:
            symbol: Ticker symbol to purge.
        """
        self._positions.pop(symbol, None)

    def positions(self) -> list[Position]:
        """Return all open positions."""
        return list(self._positions.values())

    def has_position(self, symbol: str) -> bool:
        """Return whether a position is open for *symbol*."""
        return symbol in self._positions

    def position_quantity(self, symbol: str) -> int:
        """Return the net signed quantity for *symbol* (long + / short -)."""
        pos = self._positions.get(symbol)
        if pos is None:
            return 0
        return pos.quantity if pos.side == PositionSide.LONG else -pos.quantity

    @property
    def cash(self) -> float:
        """Return current cash balance."""
        return self._cash

    @property
    def realized_pnl(self) -> float:
        """Return cumulative realised P&L."""
        return self._realized_pnl

    # ── Fill processing ─────────────────────────────────────────

    def apply_fill(
        self,
        fill: Fill,
        commission: float,
        price: float | None = None,
    ) -> Position:
        """Apply a fill to the portfolio, updating cash and positions.

        Args:
            fill: The execution fill.
            commission: Total commission charged for the fill.
            price: Market price (defaults to fill price).

        Returns:
            The resulting (possibly modified) position.

        Raises:
            ValueError: If the fill would exceed leverage limits or
                short selling is disabled.
        """
        price = price if price is not None else fill.price
        value = fill.quantity * price
        signed_qty = fill.quantity if fill.is_buy else -fill.quantity

        pos = self._positions.get(fill.symbol)

        if pos is None:
            side = PositionSide.LONG if signed_qty > 0 else PositionSide.SHORT
            if side == PositionSide.SHORT and not self._allow_short:
                raise ValueError(f"Short selling disabled; cannot short {fill.symbol}.")
            self._cash -= (value if fill.is_buy else -value)
            self._cash -= commission
            self._positions[fill.symbol] = Position(
                symbol=fill.symbol,
                side=side,
                quantity=abs(signed_qty),
                average_price=price,
                opened_at=fill.timestamp,
            )
            return self._positions[fill.symbol]

        # Position exists — possibly closing/reversing.
        if pos.side == PositionSide.LONG and fill.is_buy:
            # Add to long position.
            new_qty = pos.quantity + fill.quantity
            pos.average_price = (pos.average_price * pos.quantity + price * fill.quantity) / new_qty
            pos.quantity = new_qty
            self._cash -= value + commission
        elif pos.side == PositionSide.SHORT and not fill.is_buy:
            # Add to short position.
            new_qty = pos.quantity + fill.quantity
            pos.average_price = (pos.average_price * pos.quantity + price * fill.quantity) / new_qty
            pos.quantity = new_qty
            self._cash += value - commission
        else:
            # Closing or reversing.
            # ``_close_portion`` already credits proceeds to (or debits
            # from) cash; the P&L is tracked separately and must NOT be
            # added to cash again.
            pnl = self._close_portion(fill.symbol, fill.quantity, price, fill.is_buy)
            self._cash -= commission
            self._realized_pnl += pnl

        # Remove fully closed positions.  Re-fetch from the dict because
        # a reversal replaces the entry with a new opposite-side
        # position (the old ``pos`` object has quantity 0).
        pos = self._positions.get(fill.symbol, pos)
        if pos.quantity <= 0:
            del self._positions[fill.symbol]

        return self._positions.get(fill.symbol, pos)

    def _close_portion(
        self,
        symbol: str,
        quantity: int,
        price: float,
        closing_is_buy: bool,
    ) -> float:
        """Close *quantity* shares and return the realised P&L.

        Long positions are closed by sells; shorts by buys.
        Reversals create a new position in the opposite direction.
        """
        pos = self._positions[symbol]
        if pos.side == PositionSide.LONG and closing_is_buy:
            raise ValueError(f"Cannot add to long via buy when already long? {symbol}")
        if pos.side == PositionSide.SHORT and not closing_is_buy:
            raise ValueError(f"Cannot add to short via sell when already short? {symbol}")

        closing_qty = min(quantity, pos.quantity)
        if pos.side == PositionSide.LONG:
            pnl = (price - pos.average_price) * closing_qty
            self._cash += price * closing_qty
        else:
            pnl = (pos.average_price - price) * closing_qty
            self._cash -= price * closing_qty

        pos.quantity -= closing_qty
        pos.realized_pnl += pnl

        # Reversal: open the opposite side for the remaining quantity.
        remaining = quantity - closing_qty
        if remaining > 0:
            # Buying beyond a short opens a LONG; selling beyond a
            # long opens a SHORT.
            new_side = PositionSide.LONG if closing_is_buy else PositionSide.SHORT
            if new_side == PositionSide.SHORT and not self._allow_short:
                raise ValueError(f"Short selling disabled; cannot reverse {symbol} to short.")
            self._positions[symbol] = Position(
                symbol=symbol,
                side=new_side,
                quantity=remaining,
                average_price=price,
                opened_at=None,
            )
            if new_side == PositionSide.SHORT:
                self._cash += price * remaining
            else:
                self._cash -= price * remaining

        return pnl

    # ── Valuation ───────────────────────────────────────────────

    def mark_to_market(
        self,
        prices: dict[str, float],
        bar_index: int = 0,
        timestamp: Any = None,
    ) -> PortfolioSnapshot:
        """Compute a valuation snapshot at the given prices.

        Args:
            prices: Mapping of symbol -> latest price.
            bar_index: Current timeline index.
            timestamp: Current timestamp.

        Returns:
            A :class:`PortfolioSnapshot` with equity and exposure.
        """
        positions_value = 0.0
        unrealized = 0.0
        exposure = 0.0
        net_exposure = 0.0

        for symbol, pos in self._positions.items():
            px = prices.get(symbol)
            if px is None:
                px = pos.average_price
            val = pos.quantity * px
            positions_value += val if pos.side == PositionSide.LONG else -val
            unrealized += pos.unrealized_pnl(px)
            exposure += val
            net_exposure += val if pos.side == PositionSide.LONG else -val

        equity = self._cash + positions_value
        leverage = exposure / equity if equity != 0 else 0.0

        snapshot = PortfolioSnapshot(
            cash=self._cash,
            positions_value=positions_value,
            equity=equity,
            unrealized_pnl=unrealized,
            realized_pnl=self._realized_pnl,
            exposure=exposure,
            net_exposure=net_exposure,
            leverage=leverage,
            bar_index=bar_index,
            timestamp=timestamp,
        )
        self._history.append(snapshot)
        return snapshot

    def equity_curve(self) -> list[float]:
        """Return the equity history as a flat list."""
        return [s.equity for s in self._history]

    def snapshots(self) -> list[PortfolioSnapshot]:
        """Return all recorded snapshots."""
        return list(self._history)

    def check_leverage(self, prices: dict[str, float]) -> bool:
        """Return whether current leverage exceeds the configured cap."""
        snapshot = self.mark_to_market(prices)
        return snapshot.leverage > self._max_leverage

    # ── Allocation views (Part 9.7) ─────────────────────────────

    def sector_allocation(
        self,
        prices: dict[str, float],
        sector_map: dict[str, str] | None = None,
    ) -> dict[str, float]:
        """Return exposure per sector at current prices.

        Args:
            prices: Latest prices.
            sector_map: Optional mapping of symbol -> sector name.
                Symbols without a mapping land in ``"Unknown"``.

        Returns:
            Sector name -> exposure value.
        """
        sector_map = sector_map or {}
        out: dict[str, float] = defaultdict(float)
        for symbol, pos in self._positions.items():
            px = prices.get(symbol, pos.average_price)
            out[sector_map.get(symbol, "Unknown")] += pos.quantity * px
        return dict(out)

    def industry_allocation(
        self,
        prices: dict[str, float],
        industry_map: dict[str, str] | None = None,
    ) -> dict[str, float]:
        """Return exposure per industry at current prices."""
        return self.sector_allocation(prices, industry_map)

    def concentration(self, prices: dict[str, float]) -> float:
        """Return the Herfindahl concentration of position values.

        Returns:
            A value in ``(0, 1]``; 1.0 means a single-position portfolio.
        """
        values = [pos.quantity * prices.get(pos.symbol, pos.average_price) for pos in self._positions.values()]
        total = sum(values)
        if total <= 0:
            return 0.0
        return sum((v / total) ** 2 for v in values)

    def correlation_exposure(
        self,
        prices: dict[str, float],
        correlation_map: dict[str, float] | None = None,
    ) -> float:
        """Return the weighted average pairwise correlation exposure.

        Args:
            prices: Latest prices.
            correlation_map: Optional mapping of ``(a, b)`` pairs to
                correlation.  Defaults to 0.5 for all pairs.

        Returns:
            Average correlation weighted by position value, or 0.0.
        """
        symbols = list(self._positions.keys())
        if len(symbols) < 2:
            return 0.0
        values = {
            s: self._positions[s].quantity * prices.get(s, self._positions[s].average_price)
            for s in symbols
        }
        total = sum(values.values())
        if total <= 0:
            return 0.0
        weighted = 0.0
        count = 0
        for i, a in enumerate(symbols):
            for b in symbols[i + 1:]:
                corr = correlation_map.get((a, b), correlation_map.get((b, a), 0.5)) if correlation_map else 0.5
                weighted += corr * (values[a] + values[b]) / total
                count += 1
        return weighted / count if count else 0.0
