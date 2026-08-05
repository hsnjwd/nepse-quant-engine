"""Strategy tournament for the institutional backtesting platform.

Part 9.14 — runs multiple strategies over the same data, computes
comparable metrics, and ranks them by multiple criteria (return,
Sharpe, win rate, drawdown, profit factor, and an AI-style composite
score).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.models import BacktestConfig, Bar, Order

logger = logging.getLogger(__name__)


@dataclass
class TournamentEntry:
    """Performance of one strategy in the tournament.

    Attributes:
        name: Strategy name.
        total_return: Total fractional return.
        sharpe: Annualised Sharpe.
        sortino: Annualised Sortino.
        max_drawdown: Max drawdown percentage (0–100).
        win_rate: Win rate percentage (0–100).
        profit_factor: Gross profit / gross loss.
        trades: Number of closed trades.
        final_equity: Ending equity.
        sqn: System Quality Number.
        ai_score: Composite 0–100 quality score.
        rank: Tournament rank (1-based; 0 before ranking).
        metadata: Extra context.
    """

    name: str = ""
    total_return: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    trades: int = 0
    final_equity: float = 0.0
    sqn: float = 0.0
    ai_score: float = 0.0
    rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "name": self.name,
            "total_return": round(self.total_return, 6),
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 4),
            "trades": self.trades,
            "final_equity": round(self.final_equity, 2),
            "sqn": round(self.sqn, 4),
            "ai_score": round(self.ai_score, 2),
            "rank": self.rank,
        }


class StrategyTournament:
    """Runs and ranks a set of strategies over shared data.

    A "strategy" is any callable ``(bar_index, bars, context) ->
    list[Order]`` compatible with :class:`BacktestEngine`.

    Usage::

        tournament = StrategyTournament(config=BacktestConfig(initial_cash=1_000_000))
        entries = tournament.run(
            data={"NABIL": df},
            strategies={"Momentum": momentum_fn, "Breakout": breakout_fn},
        )
        ranking = tournament.rank(entries)
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        engine_factory: Callable[..., BacktestEngine] | None = None,
    ) -> None:
        """Initialise the tournament.

        Args:
            config: Shared backtest configuration.
            engine_factory: Optional custom engine factory.
        """
        self._config = config or BacktestConfig()
        self._engine_factory = engine_factory
        self._entries: list[TournamentEntry] = []

    def _build_engine(self, strategy: Callable[[int, dict[str, Bar], Any], list[Order]] | None = None) -> BacktestEngine:
        """Return a fresh engine for a single strategy run.

        Args:
            strategy: Strategy callable to wire into the engine (if any).

        Returns:
            A configured :class:`BacktestEngine`.
        """
        if self._engine_factory is not None:
            engine = self._engine_factory()
            if strategy is not None and engine.strategy is None:
                engine.strategy = strategy
            return engine
        return BacktestEngine(config=self._config, strategy=strategy)

    def run_strategy(
        self,
        name: str,
        strategy: Callable[[int, dict[str, Bar], Any], list[Order]],
        data: dict[str, pd.DataFrame],
    ) -> TournamentEntry:
        """Run one strategy and compute its metrics.

        The strategy is wrapped so that per-bar exceptions raised
        inside the engine are counted; a strategy that fails on every
        bar is flagged with an ``error`` metadata entry instead of
        silently producing an empty result.

        Args:
            name: Strategy display name.
            strategy: Strategy callable.
            data: Symbol -> OHLCV DataFrame mapping.

        Returns:
            A :class:`TournamentEntry`.
        """
        failures: dict[str, int] = {"count": 0}

        def _wrapped(
            bar_index: int,
            bars: dict[str, Bar],
            ctx: Any,
        ) -> list[Order]:
            try:
                return strategy(bar_index, bars, ctx)
            except Exception as exc:
                failures["count"] += 1
                logger.warning(
                    "[Tournament] %s raised at bar %d: %s", name, bar_index, exc,
                )
                raise

        engine = self._build_engine(_wrapped)
        result = engine.run(data)
        entry = self._entry_from_result(name, result)
        if failures["count"]:
            entry.metadata["error"] = f"{failures['count']} strategy errors"
        return entry

    def _entry_from_result(self, name: str, result: BacktestResult) -> TournamentEntry:
        """Build a TournamentEntry from a BacktestResult."""
        m = result.metrics
        return TournamentEntry(
            name=name,
            total_return=m.total_return,
            sharpe=m.sharpe,
            sortino=m.sortino,
            max_drawdown=m.max_drawdown,
            win_rate=m.win_rate,
            profit_factor=m.profit_factor,
            trades=m.trade_count,
            final_equity=result.equity_curve[-1] if result.equity_curve else 0.0,
            sqn=m.sqn,
            ai_score=self._ai_score(m.total_return, m.sharpe, m.max_drawdown, m.win_rate),
        )

    def run(
        self,
        data: dict[str, pd.DataFrame],
        strategies: dict[str, Callable[[int, dict[str, Bar], Any], list[Order]]],
    ) -> list[TournamentEntry]:
        """Run every strategy and return unsorted entries.

        Args:
            data: Shared data for all strategies.
            strategies: Mapping of name -> strategy callable.

        Returns:
            List of :class:`TournamentEntry`.
        """
        self._entries = []
        for name, strategy in strategies.items():
            try:
                entry = self.run_strategy(name, strategy, data)
                self._entries.append(entry)
                logger.info("Tournament: %s -> return=%.4f", name, entry.total_return)
            except Exception as exc:
                logger.exception("Tournament strategy %s failed: %s", name, exc)
                self._entries.append(TournamentEntry(name=name, metadata={"error": str(exc)}))
        return list(self._entries)

    def rank(
        self,
        entries: list[TournamentEntry] | None = None,
        key: str = "ai_score",
        reverse: bool = True,
    ) -> list[TournamentEntry]:
        """Rank entries by a metric and assign 1-based ranks.

        Args:
            entries: Entries to rank (defaults to the last run).
            key: Metric to sort by (``ai_score``, ``total_return``,
                ``sharpe``, ``win_rate``, ``sqn``).
            reverse: Sort descending when True.

        Returns:
            Sorted entries with ``rank`` populated.
        """
        source = list(entries if entries is not None else self._entries)
        valid = [e for e in source if not e.metadata.get("error")]
        valid.sort(key=lambda e: getattr(e, key, 0.0), reverse=reverse)
        for idx, entry in enumerate(valid, start=1):
            entry.rank = idx
        failed = [e for e in source if e.metadata.get("error")]
        return valid + failed

    def ranking_table(self, key: str = "ai_score") -> list[dict[str, Any]]:
        """Return the ranked table as JSON-ready dicts."""
        return [e.to_dict() for e in self.rank(key=key)]

    def entries(self) -> list[TournamentEntry]:
        """Return the entries from the last run."""
        return list(self._entries)

    @staticmethod
    def _ai_score(
        total_return: float,
        sharpe: float,
        max_drawdown: float,
        win_rate: float,
    ) -> float:
        """Compute a 0–100 composite quality score.

        Weights: return 30, Sharpe 30, drawdown 20, win rate 20.
        """
        score = 0.0

        ret_comp = max(0.0, min(total_return, 1.0))
        score += ret_comp * 30.0

        if sharpe is not None and sharpe > 0:
            score += min(sharpe, 3.0) / 3.0 * 30.0

        dd_penalty = max(0.0, min(max_drawdown / 30.0, 1.0))
        score += (1.0 - dd_penalty) * 20.0

        score += max(0.0, min(win_rate / 100.0, 1.0)) * 20.0

        return max(0.0, min(score, 100.0))
