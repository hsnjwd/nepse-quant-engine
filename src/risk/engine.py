"""Position sizing and portfolio risk engine for the NEPSE Quant Engine.

Phase 1 of the Risk Engine.  Responsible for calculating per-position
capital at risk, share counts based on fixed-fraction position sizing,
and aggregate portfolio risk metrics.

All monetary calculations use :class:`decimal.Decimal` arithmetic to
eliminate IEEE 754 binary floating-point errors.  Float values are
converted to Decimal via ``Decimal(str(value))`` for lossless
construction and converted back only when stored in :class:`RiskPosition`.

This module deliberately avoids Kelly Criterion, ATR/volatility sizing,
VaR, expected shortfall, sector risk, margin, leverage, and trailing
stops — those will be added in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

from src.logging.logger import logger


# ---------------------------------------------------------------------------
# RiskPosition — output dataclass
# ---------------------------------------------------------------------------


@dataclass
class RiskPosition:
    """A single position-sized risk calculation for one stock.

    Attributes:
        symbol:
            Stock ticker symbol.
        entry_price:
            Planned entry price per share.
        stop_loss:
            Planned stop-loss price per share.
        risk_per_share:
            Rupee risk per share (entry_price - stop_loss).
        capital_at_risk:
            Total rupees at risk for this position.
        shares:
            Whole number of shares to trade (always rounded down).
        position_value:
            Total rupee value of the position (shares x entry_price).
        risk_pct:
            Percentage of portfolio capital at risk in this position.
    """

    symbol: str
    entry_price: float
    stop_loss: float
    risk_per_share: float
    capital_at_risk: float
    shares: int
    position_value: float
    risk_pct: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with the same fields as the dataclass.
        """
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "risk_per_share": self.risk_per_share,
            "capital_at_risk": self.capital_at_risk,
            "shares": self.shares,
            "position_value": self.position_value,
            "risk_pct": self.risk_pct,
        }


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------


class RiskEngine:
    """Calculate position sizes and portfolio-level risk metrics.

    Uses fixed-fraction position sizing where each trade risks a
    configurable percentage of total capital.

    All financial calculations are performed with :class:`Decimal` to
    guarantee exact arithmetic for subtraction, multiplication, and
    division, eliminating IEEE 754 rounding errors.

    Args:
        capital:
            Total portfolio capital in NPR (must be > 0).
        risk_per_trade_pct:
            Fraction of capital to risk on a single trade, as a decimal
            (e.g. 0.02 = 2%).  Must be in (0, 1].
        max_portfolio_risk_pct:
            Maximum aggregate portfolio risk as a decimal fraction
            (e.g. 0.10 = 10%).  Must be in (0, 1].

    Raises:
        ValueError: If any parameter violates its constraints.
    """

    def __init__(
        self,
        capital: float,
        risk_per_trade_pct: float = 0.02,
        max_portfolio_risk_pct: float = 0.10,
    ) -> None:
        if capital <= 0:
            raise ValueError(
                f"Capital must be positive, got {capital}."
            )

        if not 0 < risk_per_trade_pct <= 1:
            raise ValueError(
                f"risk_per_trade_pct must be in (0, 1], "
                f"got {risk_per_trade_pct}."
            )

        if not 0 < max_portfolio_risk_pct <= 1:
            raise ValueError(
                f"max_portfolio_risk_pct must be in (0, 1], "
                f"got {max_portfolio_risk_pct}."
            )

        self._capital = capital
        self._risk_per_trade = risk_per_trade_pct
        self._max_portfolio_risk = max_portfolio_risk_pct

        logger.debug(
            "RiskEngine(capital=%.2f, risk_per_trade=%.4f, "
            "max_portfolio_risk=%.4f)",
            capital,
            risk_per_trade_pct,
            max_portfolio_risk_pct,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_position(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
    ) -> RiskPosition:
        """Calculate position size for a single trade.

        Uses fixed-fraction position sizing: the amount of capital at
        risk is ``capital x risk_per_trade_pct``.  Shares are rounded
        down so that risk never exceeds the configured fraction.

        All arithmetic is performed with :class:`Decimal` for exactness.
        Float results are produced only when constructing the return
        value.

        Args:
            symbol:
                Stock ticker symbol.
            entry_price:
                Planned entry price per share (must be > 0).
            stop_loss:
                Planned stop-loss price per share (must be > 0 and
                strictly less than entry_price).

        Returns:
            A :class:`RiskPosition` instance with the calculated values.

        Raises:
            ValueError: If entry_price or stop_loss are invalid.
        """
        if entry_price <= 0:
            raise ValueError(
                f"entry_price must be positive, got {entry_price}."
            )

        if stop_loss <= 0:
            raise ValueError(
                f"stop_loss must be positive, got {stop_loss}."
            )

        if stop_loss >= entry_price:
            raise ValueError(
                f"stop_loss ({stop_loss}) must be less than "
                f"entry_price ({entry_price})."
            )

        # ------------------------------------------------------------------
        # Decimal arithmetic — convert inputs via str() for lossless
        # construction, then compute every monetary value with Decimal.
        # ------------------------------------------------------------------

        d_entry = Decimal(str(entry_price))
        d_stop = Decimal(str(stop_loss))
        d_capital = Decimal(str(self._capital))
        d_risk_rate = Decimal(str(self._risk_per_trade))

        # risk_per_share = entry_price - stop_loss  (exact Decimal subtraction)
        d_risk_per_share = d_entry - d_stop

        # capital_at_risk = capital x risk_per_trade_pct  (exact Decimal mult.)
        d_capital_at_risk = d_capital * d_risk_rate

        # shares = floor(capital_at_risk / risk_per_share)  (exact Decimal div.)
        d_shares = (
            (d_capital_at_risk / d_risk_per_share)
            .to_integral_value(rounding=ROUND_DOWN)
        )

        # position_value = shares x entry_price  (exact Decimal mult.)
        d_position_value = d_shares * d_entry

        # Convert back to Python native types for the dataclass.
        shares_int = int(d_shares)
        entry_price_f = float(d_entry)
        stop_loss_f = float(d_stop)
        risk_per_share_f = float(d_risk_per_share)
        capital_at_risk_f = float(d_capital_at_risk)
        position_value_f = float(d_position_value)
        risk_pct_f = round(
            float(d_capital_at_risk / d_capital) * 100, 2
        )

        logger.debug(
            "Position '%s': shares=%d, risk=%.2f, value=%.2f",
            symbol,
            shares_int,
            capital_at_risk_f,
            position_value_f,
        )

        return RiskPosition(
            symbol=symbol,
            entry_price=round(entry_price_f, 2),
            stop_loss=round(stop_loss_f, 2),
            risk_per_share=round(risk_per_share_f, 2),
            capital_at_risk=round(capital_at_risk_f, 2),
            shares=shares_int,
            position_value=round(position_value_f, 2),
            risk_pct=risk_pct_f,
        )

    def calculate_multiple(
        self,
        positions: list[dict[str, Any]],
    ) -> list[RiskPosition]:
        """Calculate position sizes for a batch of trade setups.

        Each dict must contain ``symbol``, ``entry_price``, and
        ``stop_loss`` keys.  Invalid entries (missing keys, failed
        validation) are logged and skipped.

        Args:
            positions:
                A list of trade setup dictionaries.

        Returns:
            A list of :class:`RiskPosition` instances for every valid
            entry in the input.
        """
        results: list[RiskPosition] = []

        for item in positions:
            symbol = item.get("symbol", "?")
            try:
                ep = float(item.get("entry_price", 0))
                sl = float(item.get("stop_loss", 0))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping '%s' — invalid numeric values: %s.",
                    symbol,
                    exc,
                )
                continue

            try:
                result = self.calculate_position(
                    symbol=symbol,
                    entry_price=ep,
                    stop_loss=sl,
                )
                results.append(result)
            except ValueError as exc:
                logger.warning(
                    "Skipping '%s' — validation failed: %s.",
                    symbol,
                    exc,
                )

        logger.info(
            "Calculated %d positions from %d inputs.",
            len(results),
            len(positions),
        )

        return results

    def portfolio_risk(
        self,
        positions: list[RiskPosition],
    ) -> dict[str, Any]:
        """Calculate aggregate portfolio risk for a set of positions.

        Args:
            positions:
                A list of :class:`RiskPosition` instances.

        Returns:
            A dictionary containing:

            - **positions**: Number of positions.
            - **total_capital_at_risk**: Sum of capital at risk across
              all positions.
            - **portfolio_risk_pct**: Aggregate risk as a percentage of
              total capital.
            - **remaining_risk_capacity**: Capital available before
              hitting ``max_portfolio_risk_pct``.
            - **within_limit**: ``True`` if aggregate risk is within
              the configured maximum.
        """
        if not positions:
            logger.debug("portfolio_risk() received empty list.")
            return {
                "positions": 0,
                "total_capital_at_risk": 0.0,
                "portfolio_risk_pct": 0.0,
                "remaining_risk_capacity": (
                    self._capital * self._max_portfolio_risk
                ),
                "within_limit": True,
            }

        # Aggregate using Decimal for exact summation, then convert back.
        d_total_at_risk: Decimal = Decimal("0")
        d_capital = Decimal(str(self._capital))
        d_max_risk_rate = Decimal(str(self._max_portfolio_risk))

        for p in positions:
            d_total_at_risk += Decimal(str(p.capital_at_risk))

        d_portfolio_risk_pct = (
            (d_total_at_risk / d_capital) * Decimal("100")
        )
        d_max_risk_capital = d_capital * d_max_risk_rate
        d_remaining = d_max_risk_capital - d_total_at_risk

        total_at_risk_f = float(d_total_at_risk)
        portfolio_risk_pct_f = round(float(d_portfolio_risk_pct), 2)
        remaining_f = round(float(d_remaining), 2)
        within = d_total_at_risk <= d_max_risk_capital

        logger.info(
            "Portfolio risk: %.2f (%.2f%% of capital) — %s limit.",
            total_at_risk_f,
            portfolio_risk_pct_f,
            "within" if within else "exceeds",
        )

        return {
            "positions": len(positions),
            "total_capital_at_risk": round(total_at_risk_f, 2),
            "portfolio_risk_pct": portfolio_risk_pct_f,
            "remaining_risk_capacity": remaining_f,
            "within_limit": within,
        }
