"""Strategy Performance Analytics Engine for the NEPSE Quant Engine.

Analyses completed backtest results and produces detailed performance
statistics for individual strategies, as well as strategy rankings.

This module is purely analytical — it does NOT perform any trading or
backtesting itself.  It consumes the trade history output produced by
:class:`~src.backtest.engine.BacktestEngine` and computes:

- Basic trade statistics (win rate, profit factor, expectancy)
- Advanced risk-adjusted metrics (Sharpe, Sortino, Calmar ratios)
- A 0–100 quality score with a qualitative rating label
- Cross-strategy rankings sorted by score
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import fmean, pstdev
from typing import Any

from src.logging.logger import logger


# ======================================================================
# Constants — rating thresholds
# ======================================================================

_RATING_TABLE: list[tuple[float, str]] = [
    (95.0, "Elite"),
    (85.0, "Excellent"),
    (70.0, "Good"),
    (55.0, "Average"),
    (40.0, "Weak"),
]


def _lookup_rating(score: float) -> str:
    """Map a numeric score (0–100) to a qualitative rating label.

    Args:
        score: The quality score.

    Returns:
        One of ``"Elite"``, ``"Excellent"``, ``"Good"``, ``"Average"``,
        ``"Weak"``, or ``"Poor"``.
    """
    for threshold, label in _RATING_TABLE:
        if score >= threshold:
            return label
    return "Poor"


# ======================================================================
# StrategyPerformance — output dataclass
# ======================================================================


@dataclass
class StrategyPerformance:
    """Comprehensive performance report for a single strategy backtest.

    Attributes:
        strategy_name:
            Name of the strategy being analysed.
        symbol:
            Stock ticker symbol traded.
        total_trades:
            Total number of completed trades.
        winning_trades:
            Number of trades with positive net profit.
        losing_trades:
            Number of trades with negative net profit.
        breakeven_trades:
            Number of trades with zero net profit.
        win_rate:
            Percentage of trades that were profitable (0–100).
        profit_factor:
            Ratio of gross profit to absolute gross loss.  ``0.0`` when
            there are no losing trades.
        expectancy:
            Mean net profit per trade in NPR (not percentage).
        gross_profit:
            Sum of all positive net profits in NPR.
        gross_loss:
            Absolute sum of all negative net profits in NPR.
        net_profit:
            Gross profit minus gross loss in NPR.
        average_win:
            Mean net profit of winning trades in NPR.
        average_loss:
            Mean net loss of losing trades in NPR (negative value).
        average_return_pct:
            Mean percentage return per trade.
        average_holding_days:
            Mean holding period in days/candles.
        largest_win:
            Largest single-trade net profit in NPR.
        largest_loss:
            Largest single-trade net loss in NPR (negative value).
        max_drawdown:
            Maximum peak-to-trough decline as a positive percentage.
        recovery_factor:
            Net profit divided by absolute max drawdown (in NPR).
        sharpe_ratio:
            Annualised Sharpe ratio, or ``None`` when insufficient data.
        sortino_ratio:
            Annualised Sortino ratio, or ``None`` when insufficient data.
        calmar_ratio:
            Annualised return divided by max drawdown, or ``None`` when
            max drawdown is zero or insufficient data.
        ending_capital:
            Simulated ending portfolio capital after all trades.
        return_pct:
            Total portfolio return as a percentage.
        score:
            Composite quality score (0–100).
        rating:
            Qualitative label derived from ``score``.
        metadata:
            Additional context (e.g. analysis timestamp, version).
    """

    strategy_name: str
    symbol: str

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0

    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0

    average_win: float = 0.0
    average_loss: float = 0.0
    average_return_pct: float = 0.0
    average_holding_days: float = 0.0

    largest_win: float = 0.0
    largest_loss: float = 0.0

    max_drawdown: float = 0.0
    recovery_factor: float = 0.0

    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None

    ending_capital: float = 0.0
    return_pct: float = 0.0

    score: float = 0.0
    rating: str = "Poor"

    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with all performance fields, ready for API output
            or logging.
        """
        return {
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "breakeven_trades": self.breakeven_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "net_profit": self.net_profit,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "average_return_pct": self.average_return_pct,
            "average_holding_days": self.average_holding_days,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "max_drawdown": self.max_drawdown,
            "recovery_factor": self.recovery_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "ending_capital": self.ending_capital,
            "return_pct": self.return_pct,
            "score": self.score,
            "rating": self.rating,
            "metadata": dict(self.metadata),
        }


# ======================================================================
# PerformanceAnalyzer
# ======================================================================


class PerformanceAnalyzer:
    """Analyse completed backtest trade histories.

    The analyser is stateless — each call to :meth:`analyze` is
    independent.  It accepts the list of trade records produced by
    :func:`~src.backtest.engine.run_backtest` and returns a
    :class:`StrategyPerformance` dataclass with both basic and
    risk-adjusted metrics.

    Usage::

        from src.analytics.performance import PerformanceAnalyzer

        analyzer = PerformanceAnalyzer()
        perf = analyzer.analyze(
            strategy_name="MomentumStrategy",
            symbol="NABIL",
            starting_capital=1_000_000,
            trade_history=trades,
        )
        print(perf.to_dict())
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        strategy_name: str,
        symbol: str,
        starting_capital: float,
        trade_history: list[dict[str, Any]],
    ) -> StrategyPerformance:
        """Analyse a completed backtest trade history.

        Computes basic trade statistics, risk-adjusted performance
        ratios, a simulated equity curve, and a composite quality score.

        Args:
            strategy_name:
                Name of the strategy that generated the trades.
            symbol:
                Stock ticker symbol that was traded.
            starting_capital:
                Starting portfolio capital in NPR.
            trade_history:
                List of trade record dicts as returned by
                :func:`~src.backtest.engine.run_backtest`.  Each dict
                must contain at least ``net_profit``, ``return_pct``,
                and ``holding_days`` keys.

        Returns:
            A :class:`StrategyPerformance` instance with all computed
            metrics.
        """
        logger.debug(
            "Analysing '%s' on '%s' with %d trades.",
            strategy_name,
            symbol,
            len(trade_history),
        )

        try:
            return self._analyze_impl(
                strategy_name=strategy_name,
                symbol=symbol,
                starting_capital=starting_capital,
                trade_history=trade_history,
            )
        except Exception as exc:
            logger.exception(
                "Performance analysis failed for '%s' on '%s': %s",
                strategy_name,
                symbol,
                exc,
            )
            return StrategyPerformance(
                strategy_name=strategy_name,
                symbol=symbol,
                metadata={"error": str(exc)},
            )

    def analyze_to_dict(
        self,
        strategy_name: str,
        symbol: str,
        starting_capital: float,
        trade_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Convenience method returning the analysis as a dictionary.

        Equivalent to ``analyze(...).to_dict()``.

        Args:
            strategy_name:
                Name of the strategy.
            symbol:
                Stock ticker symbol.
            starting_capital:
                Starting portfolio capital.
            trade_history:
                List of trade record dicts.

        Returns:
            JSON-serialisable performance dictionary.
        """
        return self.analyze(
            strategy_name=strategy_name,
            symbol=symbol,
            starting_capital=starting_capital,
            trade_history=trade_history,
        ).to_dict()

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _analyze_impl(
        self,
        strategy_name: str,
        symbol: str,
        starting_capital: float,
        trade_history: list[dict[str, Any]],
    ) -> StrategyPerformance:
        """Core analysis logic (separated for error-handling)."""
        if not trade_history:
            logger.info(
                "Empty trade history for '%s' on '%s' — returning zeroed metrics.",
                strategy_name,
                symbol,
            )
            return StrategyPerformance(
                strategy_name=strategy_name,
                symbol=symbol,
                ending_capital=starting_capital,
                metadata={"empty_history": True},
            )

        # ---- Extract raw data ----
        net_profits: list[float] = [
            float(t.get("net_profit", 0.0)) for t in trade_history
        ]
        return_pcts: list[float] = [
            float(t.get("return_pct", 0.0)) for t in trade_history
        ]
        holding_days: list[float] = [
            float(t.get("holding_days", 0)) for t in trade_history
        ]

        # ---- Basic counts ----
        total = len(trade_history)
        wins = sum(1 for v in net_profits if v > 0)
        losses = sum(1 for v in net_profits if v < 0)
        breakeven = sum(1 for v in net_profits if v == 0)

        win_rate = (wins / total * 100.0) if total else 0.0

        # ---- Gross profit / loss ----
        gross_profit = sum(v for v in net_profits if v > 0)
        gross_loss = abs(sum(v for v in net_profits if v < 0))
        net_profit = gross_profit - gross_loss

        # ---- Averages ----
        average_win = fmean([v for v in net_profits if v > 0]) if wins else 0.0
        average_loss = fmean([v for v in net_profits if v < 0]) if losses else 0.0
        average_return_pct = fmean(return_pcts) if return_pcts else 0.0
        average_holding_days = fmean(holding_days) if holding_days else 0.0

        # ---- Extremes ----
        largest_win = max(net_profits) if net_profits else 0.0
        largest_loss = min(net_profits) if net_profits else 0.0

        # ---- Profit factor ----
        profit_factor = (
            gross_profit / gross_loss if gross_loss else 0.0
        )

        # ---- Expectancy (mean net profit) ----
        expectancy = fmean(net_profits) if net_profits else 0.0

        # ---- Simulated equity curve ----
        equity_curve = self._build_equity_curve(
            starting_capital=starting_capital,
            net_profits=net_profits,
        )
        ending_capital = equity_curve[-1] if equity_curve else starting_capital
        return_pct = (
            (ending_capital - starting_capital) / starting_capital * 100.0
            if starting_capital else 0.0
        )

        # ---- Maximum drawdown ----
        max_drawdown = self._compute_max_drawdown(equity_curve) if equity_curve else 0.0

        # ---- Recovery factor ----
        drawdown_npr = self._max_drawdown_npr(equity_curve)
        recovery_factor = (
            net_profit / abs(drawdown_npr) if drawdown_npr else 0.0
        )

        # ---- Risk-adjusted ratios ----
        sharpe_ratio = self._compute_sharpe(return_pcts)
        sortino_ratio = self._compute_sortino(return_pcts)
        calmar_ratio = self._compute_calmar(return_pct, max_drawdown)

        # ---- Composite score ----
        score = self._compute_score(
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            starting_capital=starting_capital,
        )
        rating = _lookup_rating(score)

        logger.info(
            "Analysis for '%s' on '%s': score=%.1f rating=%s "
            "trades=%d win_rate=%.1f%% net=%.2f",
            strategy_name,
            symbol,
            score,
            rating,
            total,
            win_rate,
            net_profit,
        )

        return StrategyPerformance(
            strategy_name=strategy_name,
            symbol=symbol,
            total_trades=total,
            winning_trades=wins,
            losing_trades=losses,
            breakeven_trades=breakeven,
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            expectancy=round(expectancy, 2),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_profit=round(net_profit, 2),
            average_win=round(average_win, 2),
            average_loss=round(average_loss, 2),
            average_return_pct=round(average_return_pct, 2),
            average_holding_days=round(average_holding_days, 2),
            largest_win=round(largest_win, 2),
            largest_loss=round(largest_loss, 2),
            max_drawdown=round(max_drawdown, 2),
            recovery_factor=round(recovery_factor, 2),
            sharpe_ratio=round(sharpe_ratio, 4) if sharpe_ratio is not None else None,
            sortino_ratio=round(sortino_ratio, 4) if sortino_ratio is not None else None,
            calmar_ratio=round(calmar_ratio, 4) if calmar_ratio is not None else None,
            ending_capital=round(ending_capital, 2),
            return_pct=round(return_pct, 2),
            score=round(score, 2),
            rating=rating,
            metadata={},
        )

    # ------------------------------------------------------------------
    # Static helpers — equity curve
    # ------------------------------------------------------------------

    @staticmethod
    def _build_equity_curve(
        starting_capital: float,
        net_profits: list[float],
    ) -> list[float]:
        """Simulate an equity curve by compounding net profits.

        Args:
            starting_capital: Initial portfolio capital.
            net_profits: Ordered list of per-trade net profits.

        Returns:
            List of equity values including the starting capital at
            position 0.
        """
        curve: list[float] = [starting_capital]
        for pnl in net_profits:
            curve.append(curve[-1] + pnl)
        return curve

    # ------------------------------------------------------------------
    # Static helpers — drawdown
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_max_drawdown(equity_curve: list[float]) -> float:
        """Compute maximum peak-to-trough drawdown as a percentage.

        Args:
            equity_curve: Ordered equity values (index 0 = starting capital).

        Returns:
            Maximum drawdown as a positive percentage, or ``0.0``.
        """
        peak: float | None = None
        max_dd = 0.0
        for value in equity_curve:
            if peak is None or value > peak:
                peak = value
            if peak and peak > 0 and value < peak:
                dd = (peak - value) / peak * 100.0
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    @staticmethod
    def _max_drawdown_npr(equity_curve: list[float]) -> float:
        """Compute maximum peak-to-trough drawdown in NPR (absolute).

        Args:
            equity_curve: Ordered equity values.

        Returns:
            Maximum drawdown as a positive NPR amount, or ``0.0``.
        """
        peak: float | None = None
        max_dd = 0.0
        for value in equity_curve:
            if peak is None or value > peak:
                peak = value
            if peak and value < peak:
                dd = peak - value
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    # ------------------------------------------------------------------
    # Static helpers — risk-adjusted ratios
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sharpe(return_pcts: list[float]) -> float | None:
        """Compute the annualised Sharpe ratio from per-trade returns.

        Uses the square root of 252 (trading days per year) for
        annualisation.  Returns ``None`` when there are fewer than 2
        trades.

        Args:
            return_pcts: List of per-trade percentage returns.

        Returns:
            Annualised Sharpe ratio, or ``None``.
        """
        if len(return_pcts) < 2:
            return None
        mean_r = fmean(return_pcts)
        std_r = pstdev(return_pcts)
        if std_r == 0:
            return None
        # Annualise: multiply by sqrt(252)
        return (mean_r / std_r) * math.sqrt(252)

    @staticmethod
    def _compute_sortino(return_pcts: list[float]) -> float | None:
        """Compute the annualised Sortino ratio from per-trade returns.

        Uses semi-deviation from zero (downside risk) and annualises
        with ``sqrt(252)``.  Returns ``None`` when there are fewer than
        2 trades or when downside deviation is zero.

        Args:
            return_pcts: List of per-trade percentage returns.

        Returns:
            Annualised Sortino ratio, or ``None``.
        """
        if len(return_pcts) < 2:
            return None
        mean_r = fmean(return_pcts)
        # Semi-deviation from zero: sqrt(mean(min(0, r)^2))
        semi_variance = fmean(
            [min(0.0, r) ** 2 for r in return_pcts]
        )
        if semi_variance == 0.0:
            return None
        downside_std = math.sqrt(semi_variance)
        return (mean_r / downside_std) * math.sqrt(252)

    @staticmethod
    def _compute_calmar(
        return_pct: float,
        max_drawdown: float,
    ) -> float | None:
        """Compute the Calmar ratio (return / max drawdown).

        Returns ``None`` when max drawdown is zero or return is
        negative and drawdown is zero.

        Args:
            return_pct: Total portfolio return as a percentage.
            max_drawdown: Maximum drawdown as a positive percentage.

        Returns:
            Calmar ratio, or ``None``.
        """
        if max_drawdown <= 0:
            return None
        return return_pct / max_drawdown

    # ------------------------------------------------------------------
    # Static helpers — composite score
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_score(
        win_rate: float,
        profit_factor: float,
        expectancy: float,
        max_drawdown: float,
        sharpe_ratio: float | None,
        starting_capital: float,
    ) -> float:
        """Compute a 0–100 composite quality score.

        Weighting scheme:

        - Win rate:     25 points
        - Profit factor: 25 points
        - Expectancy:    20 points
        - Drawdown:      15 points
        - Sharpe ratio:  15 points

        Each component is normalised and clamped before summing.

        Args:
            win_rate: Win rate as a percentage (0–100).
            profit_factor: Ratio of gross profit to gross loss.
            expectancy: Mean net profit per trade in NPR.
            max_drawdown: Maximum drawdown as a positive percentage.
            sharpe_ratio: Annualised Sharpe ratio, or ``None``.
            starting_capital: Starting portfolio capital.

        Returns:
            Score clamped to [0, 100].
        """
        score = 0.0

        # 1. Win rate (max 25 pts)
        wr_score = (win_rate / 100.0) * 25.0
        score += max(0.0, min(25.0, wr_score))

        # 2. Profit factor (max 25 pts) — cap PF at 5.0
        pf_normalised = min(max(profit_factor, 0.0), 5.0)
        pf_score = (pf_normalised / 5.0) * 25.0
        score += max(0.0, min(25.0, pf_score))

        # 3. Expectancy (max 20 pts) — normalise relative to starting capital
        #    A 2% expectancy per trade gets full marks.
        if starting_capital > 0:
            exp_ratio = expectancy / starting_capital
            exp_normalised = min(max(exp_ratio, 0.0), 0.02)
            exp_score = (exp_normalised / 0.02) * 20.0
        else:
            exp_score = 0.0
        score += max(0.0, min(20.0, exp_score))

        # 4. Drawdown (max 15 pts) — lower is better
        #    0% DD = 15 pts, 30%+ DD = 0 pts
        dd_penalty = min(max(max_drawdown / 30.0, 0.0), 1.0)
        dd_score = (1.0 - dd_penalty) * 15.0
        score += max(0.0, min(15.0, dd_score))

        # 5. Sharpe ratio (max 15 pts) — cap at 3.0
        if sharpe_ratio is not None and sharpe_ratio > 0:
            sharpe_normalised = min(sharpe_ratio, 3.0)
            sharpe_score = (sharpe_normalised / 3.0) * 15.0
        else:
            sharpe_score = 0.0
        score += max(0.0, min(15.0, sharpe_score))

        return max(0.0, min(100.0, score))


# ======================================================================
# StrategyRanking
# ======================================================================


@dataclass
class RankEntry:
    """A single entry in a strategy ranking table.

    Attributes:
        rank: Ordinal rank (1-based).
        strategy_name: Name of the strategy.
        score: Composite quality score (0–100).
        rating: Qualitative rating label.
        win_rate: Win rate percentage.
        profit_factor: Profit factor.
        net_profit: Net profit in NPR.
    """

    rank: int
    strategy_name: str
    score: float
    rating: str
    win_rate: float
    profit_factor: float
    net_profit: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "rank": self.rank,
            "strategy_name": self.strategy_name,
            "score": self.score,
            "rating": self.rating,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "net_profit": self.net_profit,
        }


class StrategyRanking:
    """Rank multiple strategies by their performance scores.

    Usage::

        rankings = StrategyRanking()
        entries = rankings.rank(performances)
        print(rankings.rank_to_dict(performances))
    """

    def rank(
        self,
        performances: list[StrategyPerformance],
    ) -> list[RankEntry]:
        """Sort strategy performances by score (highest first).

        Args:
            performances: List of :class:`StrategyPerformance` instances
                to rank.

        Returns:
            A list of :class:`RankEntry` instances sorted by descending
            score with 1-based ranks.
        """
        sorted_perfs = sorted(
            performances,
            key=lambda p: p.score,
            reverse=True,
        )

        entries: list[RankEntry] = []
        for idx, perf in enumerate(sorted_perfs, start=1):
            entries.append(
                RankEntry(
                    rank=idx,
                    strategy_name=perf.strategy_name,
                    score=perf.score,
                    rating=perf.rating,
                    win_rate=perf.win_rate,
                    profit_factor=perf.profit_factor,
                    net_profit=perf.net_profit,
                )
            )

        logger.debug(
            "Ranked %d strategies (top: %s = %.2f).",
            len(entries),
            entries[0].strategy_name if entries else "N/A",
            entries[0].score if entries else 0.0,
        )

        return entries

    def rank_to_dict(
        self,
        performances: list[StrategyPerformance],
    ) -> list[dict[str, Any]]:
        """Convenience method returning the ranking as a list of dicts.

        Equivalent to ``[e.to_dict() for e in rank(performances)]``.

        Args:
            performances: List of :class:`StrategyPerformance` instances.

        Returns:
            List of ranking entries as dictionaries.
        """
        return [e.to_dict() for e in self.rank(performances)]
