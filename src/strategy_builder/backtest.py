"""Strategy Builder — instant backtest for rule-based strategies.

Runs a simple long-only backtest of a :class:`RuleBasedStrategy` over
an OHLCV DataFrame and returns performance metrics compatible with the
existing backtest engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.logging.logger import logger

logger = logger.getChild("backtest") if logger.name else logging.getLogger(__name__)


@dataclass
class InstantBacktestResult:
    """Result of an instant strategy backtest.

    Attributes:
        strategy: Strategy name.
        initial_capital: Starting capital.
        final_equity: Ending equity value.
        total_return_pct: Total percentage return.
        trades: List of executed trade records.
        win_rate: Percentage of profitable trades.
        max_drawdown_pct: Maximum peak-to-trough drawdown.
        sharpe_ratio: Annualized Sharpe estimate.
        trade_count: Number of executed trades.
    """

    strategy: str = ""
    initial_capital: float = 100_000.0
    final_equity: float = 100_000.0
    total_return_pct: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    trade_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "strategy": self.strategy,
            "initial_capital": round(self.initial_capital, 2),
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "win_rate": round(self.win_rate, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "trade_count": self.trade_count,
        }


class InstantBacktester:
    """Execute quick backtests for rule-based strategies.

    Usage::

        backtester = InstantBacktester()
        result = backtester.run(strategy, df, capital=100000.0)
    """

    def run(
        self,
        strategy: Any,
        df: Any,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.0,
    ) -> InstantBacktestResult:
        """Run the backtest.

        Args:
            strategy: A :class:`RuleBasedStrategy` (or any object with
                ``generate_signal(df)``).
            df: OHLCV DataFrame.
            initial_capital: Starting capital.
            commission_pct: Per-trade commission as a fraction.

        Returns:
            An :class:`InstantBacktestResult`.
        """
        if len(df) < 30:
            return InstantBacktestResult(
                strategy=strategy.name,
                initial_capital=initial_capital,
                final_equity=initial_capital,
            )

        enriched = _enrich(df)
        equity = initial_capital
        cash = initial_capital
        position = 0.0  # units held
        entry_price = 0.0
        trades: list[dict[str, Any]] = []
        equity_curve = [initial_capital]

        for i in range(len(enriched)):
            row = enriched.iloc[i]
            price = float(row["Close"])

            # Evaluate entry/exit via the full-frame signal is expensive;
            # use the row-based rules directly when available.
            signal = self._row_signal(strategy, enriched, i)

            if signal == "BUY" and position == 0 and cash > 0:
                units = (cash * (1 - commission_pct)) / price
                position = units
                entry_price = price
                cash = 0.0
            elif signal == "SELL" and position > 0:
                gross = position * price
                net = gross * (1 - commission_pct)
                cash = net
                trades.append(
                    {
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(price, 2),
                        "return_pct": round((price / entry_price - 1) * 100, 2),
                        "profit": round(net - entry_price * position, 2),
                    }
                )
                position = 0.0

            equity_curve.append(cash + position * price)

        final_equity = equity_curve[-1]
        total_return = (final_equity / initial_capital - 1) * 100
        wins = sum(1 for t in trades if t["profit"] > 0)
        win_rate = (wins / len(trades) * 100) if trades else 0.0
        max_dd = _max_drawdown(equity_curve)
        sharpe = _sharpe(equity_curve)

        result = InstantBacktestResult(
            strategy=strategy.name,
            initial_capital=initial_capital,
            final_equity=round(final_equity, 2),
            total_return_pct=round(total_return, 2),
            trades=trades,
            win_rate=round(win_rate, 2),
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 4),
            trade_count=len(trades),
        )
        logger.info(
            "Instant backtest '%s': return=%.2f%% trades=%d",
            strategy.name,
            total_return,
            len(trades),
        )
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _row_signal(strategy: Any, enriched: Any, index: int) -> str:
        """Extract the signal for one row without re-enriching."""
        try:
            from src.strategy_builder.builder import evaluate_rules

            entry_rules = getattr(strategy, "entry_rules", None) or getattr(
                strategy, "_entry_rules", None
            )
            exit_rules = getattr(strategy, "exit_rules", None)
            if exit_rules is None:
                exit_rules = getattr(strategy, "_exit_rules", None)

            entry = evaluate_rules(entry_rules, enriched, index)
            exit_hit = False
            if exit_rules is not None:
                exit_hit = evaluate_rules(exit_rules, enriched, index)
            if entry and not exit_hit:
                return "BUY"
            if exit_hit:
                return "SELL"
            return "HOLD"
        except Exception:
            return "HOLD"


def _enrich(df: Any) -> Any:
    """Enrich a DataFrame with indicators."""
    from src.strategy_builder.builder import enrich_indicators

    return enrich_indicators(df)


def _max_drawdown(curve: list[float]) -> float:
    """Compute maximum drawdown percentage from an equity curve."""
    peak = curve[0]
    max_dd = 0.0
    for value in curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _sharpe(curve: list[float]) -> float:
    """Compute an annualized Sharpe estimate from daily equity changes."""
    import numpy as np

    if len(curve) < 3:
        return 0.0
    returns = np.diff(np.asarray(curve, dtype=float)) / np.asarray(curve[:-1], dtype=float)
    if len(returns) == 0:
        return 0.0
    std = float(np.std(returns))
    if std == 0:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(252))
