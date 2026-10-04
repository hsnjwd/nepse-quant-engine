"""Capital allocation engine for the NEPSE Quant Engine.

Responsible for converting strategy signals into concrete position-level
capital allocation decisions using equal-weight distribution.

This is Phase 1 of the Portfolio Engine and deliberately avoids
optimisation algorithms such as Kelly Criterion, risk parity, or
mean-variance optimisation — those will be added in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from src.logging.logger import logger

# ---------------------------------------------------------------------------
# PositionAllocation — output dataclass
# ---------------------------------------------------------------------------


@dataclass
class PositionAllocation:
    """A single capital allocation decision for one stock.

    Attributes:
        symbol:
            Stock ticker symbol.
        price:
            Reference price used for the allocation.
        weight:
            Portfolio weight allocated to this position (0–1).
        capital:
            Rupee amount allocated to this position.
        shares:
            Whole number of shares purchased (always rounded down).
        remaining_cash:
            Cash left over after rounding down the share count.
    """

    symbol: str
    price: float
    weight: float
    capital: float
    shares: int
    remaining_cash: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with the same fields as the dataclass.
        """
        return {
            "symbol": self.symbol,
            "price": self.price,
            "weight": self.weight,
            "capital": self.capital,
            "shares": self.shares,
            "remaining_cash": self.remaining_cash,
        }


# ---------------------------------------------------------------------------
# PortfolioAllocator
# ---------------------------------------------------------------------------


class PortfolioAllocator:
    """Distribute capital across a set of BUY signals.

    The allocator uses equal-weight distribution among qualifying signals.
    Non-BUY signals, signals with missing or invalid prices are silently
    skipped.

    Args:
        capital:
            Total investable capital in NPR (must be > 0).
        max_position_weight:
            Maximum allowed weight for any single position, as a decimal
            fraction (must be in the range (0, 1]).

    Raises:
        ValueError: If ``capital`` is not positive or
            ``max_position_weight`` is outside the valid range.
    """

    def __init__(
        self,
        capital: float,
        max_position_weight: float = 0.20,
    ) -> None:
        if capital <= 0:
            raise ValueError(
                f"Capital must be positive, got {capital}."
            )

        if not 0 < max_position_weight <= 1:
            raise ValueError(
                f"max_position_weight must be in (0, 1], "
                f"got {max_position_weight}."
            )

        self._capital = capital
        self._max_weight = max_position_weight

        logger.debug(
            "PortfolioAllocator(capital=%.2f, max_position_weight=%.4f)",
            capital,
            max_position_weight,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate_equal_weight(
        self,
        signals: list[dict[str, Any]],
    ) -> list[PositionAllocation]:
        """Allocate capital equally across all valid BUY signals.

        Only signals with ``signal == "BUY"``, a present price > 0 are
        considered.  HOLD, SELL, and malformed signals are silently
        skipped.

        Each qualifying position receives an equal weight of ``1 / N``
        where ``N`` is the number of valid BUY signals.

        Shares are rounded down via ``floor``; leftover cash accumulates
        as ``remaining_cash`` on the final position.

        Args:
            signals:
                A list of signal dictionaries.  Each must contain at
                least ``symbol``, ``signal``, and ``price`` keys.

        Returns:
            A list of :class:`PositionAllocation` instances, one per
            qualifying BUY signal.  Returns an empty list when no valid
            BUY signals are present.
        """
        buy_signals = self._filter_buy_signals(signals)

        if not buy_signals:
            logger.info(
                "No valid BUY signals found; returning empty allocation."
            )
            return []

        num_positions = len(buy_signals)
        raw_weight = 1.0 / num_positions

        allocations: list[PositionAllocation] = []
        capital_used = 0.0

        for i, signal in enumerate(buy_signals):
            symbol: str = signal["symbol"]
            price: float = signal["price"]

            capital_per_position = self._capital * raw_weight

            if price <= 0:
                logger.warning(
                    "Skipping '%s' — non-positive price %.4f.",
                    symbol,
                    price,
                )
                continue

            shares = int(floor(capital_per_position / price))
            allocated = shares * price

            # Only the last position tracks actual remaining portfolio cash.
            # Cash leftovers from earlier positions are absorbed here.
            if i == num_positions - 1:
                remaining_cash = self._capital - (capital_used + allocated)
            else:
                remaining_cash = 0.0

            allocation = PositionAllocation(
                symbol=symbol,
                price=price,
                weight=raw_weight,
                capital=round(allocated, 2),
                shares=shares,
                remaining_cash=round(remaining_cash, 2),
            )
            allocations.append(allocation)
            capital_used += allocated

        logger.info(
            "Allocated %.2f across %d positions "
            "(cash remaining: %.2f).",
            capital_used,
            len(allocations),
            self._capital - capital_used,
        )

        return allocations

    def portfolio_summary(
        self,
        allocations: list[PositionAllocation],
    ) -> dict[str, Any]:
        """Produce a summary dict for a set of allocations.

        Args:
            allocations:
                Position allocations to summarise.

        Returns:
            Dictionary with aggregate portfolio metrics.
        """
        capital_allocated = sum(
            a.capital for a in allocations
        )
        symbols = [a.symbol for a in allocations]

        return {
            "total_positions": len(allocations),
            "capital_allocated": round(capital_allocated, 2),
            "cash_remaining": round(
                self._capital - capital_allocated, 2
            ),
            "weights": {
                a.symbol: a.weight for a in allocations
            },
            "symbols": symbols,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_buy_signals(
        signals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return only valid BUY signals with a positive price.

        Args:
            signals: Raw signal list.

        Returns:
            Filtered list containing only BUY signals that have a
            ``symbol`` key and a ``price`` that is a positive number.
        """
        buy_signals: list[dict[str, Any]] = []
        skipped = 0

        for signal in signals:
            sig = signal.get("signal", "").upper().strip()

            if sig != "BUY":
                skipped += 1
                continue

            symbol = signal.get("symbol")
            price = signal.get("price")

            if not symbol:
                skipped += 1
                continue

            if price is None:
                logger.debug(
                    "Skipping '%s' — missing price.", symbol
                )
                skipped += 1
                continue

            try:
                price_f = float(price)
            except (TypeError, ValueError):
                logger.debug(
                    "Skipping '%s' — non-numeric price %r.",
                    symbol,
                    price,
                )
                skipped += 1
                continue

            if price_f <= 0:
                logger.debug(
                    "Skipping '%s' — non-positive price %.4f.",
                    symbol,
                    price_f,
                )
                skipped += 1
                continue

            buy_signals.append(
                {
                    "symbol": str(symbol).upper(),
                    "signal": "BUY",
                    "price": price_f,
                }
            )

        if skipped:
            logger.debug(
                "Filtered out %d non-BUY or invalid signals.",
                skipped,
            )

        return buy_signals
