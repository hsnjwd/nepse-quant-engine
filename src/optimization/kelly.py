"""Kelly Criterion position sizing.

Computes the optimal fraction of capital to risk per trade using the
Kelly formula, including a conservative fractional variant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nepse.optimization.kelly")


@dataclass
class KellyResult:
    """Kelly criterion calculation output.

    Attributes:
        full_kelly: Full Kelly fraction (0–1).
        half_kelly: Half-Kelly fraction (conservative).
        quarter_kelly: Quarter-Kelly fraction (very conservative).
        edge: Win probability.
        odds: Win/loss payoff ratio.
        notes: List of cautionary notes.
    """

    full_kelly: float = 0.0
    half_kelly: float = 0.0
    quarter_kelly: float = 0.0
    edge: float = 0.0
    odds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "full_kelly": round(self.full_kelly, 6),
            "half_kelly": round(self.half_kelly, 6),
            "quarter_kelly": round(self.quarter_kelly, 6),
            "edge": round(self.edge, 6),
            "odds": round(self.odds, 6),
            "notes": self.notes,
        }


class KellyCriterion:
    """Compute Kelly fractions from historical trade statistics.

    Usage::

        kelly = KellyCriterion()
        result = kelly.calculate(win_rate=0.55, avg_win=100.0, avg_loss=80.0)
    """

    def calculate(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> KellyResult:
        """Compute Kelly fractions.

        Args:
            win_rate: Fraction of winning trades (0–1).
            avg_win: Average gross profit per winning trade.
            avg_loss: Average gross loss per losing trade (positive).

        Returns:
            A :class:`KellyResult`.
        """
        win_rate = float(win_rate)
        avg_win = float(avg_win)
        avg_loss = float(avg_loss)

        if not 0 <= win_rate <= 1:
            raise ValueError("win_rate must be between 0 and 1.")
        if avg_win < 0:
            raise ValueError("avg_win must be non-negative.")
        if avg_loss <= 0:
            raise ValueError("avg_loss must be positive.")

        if avg_loss > 0:
            odds = avg_win / avg_loss
        else:
            odds = 0.0

        if odds > 0:
            full = (win_rate * odds - (1 - win_rate)) / odds
        else:
            full = win_rate - 0.5

        full = max(0.0, min(1.0, full))

        notes: list[str] = []
        if full <= 0:
            notes.append(
                "Kelly indicates no edge — consider not trading this "
                "system until statistics improve."
            )
        if win_rate < 0.5:
            notes.append(
                "Sub-50% win rate; Kelly relies on positive payoff odds."
            )
        notes.append(
            "Use half-Kelly or quarter-Kelly in practice to account for "
            "estimation error."
        )

        return KellyResult(
            full_kelly=full,
            half_kelly=full / 2,
            quarter_kelly=full / 4,
            edge=win_rate,
            odds=odds,
            notes=notes,
        )

    @classmethod
    def from_trades(cls, trades: list[dict[str, Any]]) -> KellyResult:
        """Compute Kelly from a list of trade records.

        Args:
            trades: Trade dicts with a ``profit`` or ``net_profit`` key.

        Returns:
            A :class:`KellyResult`.
        """
        if not trades:
            return KellyResult(notes=["No trades available."])

        profits: list[float] = []
        for trade in trades:
            value = trade.get("profit", trade.get("net_profit", 0.0))
            profits.append(float(value))

        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]
        win_rate = len(wins) / len(profits)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1.0
        return cls().calculate(win_rate, avg_win, avg_loss)
