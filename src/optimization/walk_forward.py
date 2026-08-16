"""Walk-Forward Optimisation Engine for the NEPSE Quant Engine.

Evaluates whether a strategy remains profitable on unseen data by
repeatedly training on one historical window and testing on the next
rolling window.

This module does NOT modify any existing backtest, strategy, or
analytics code.  It orchestrates the existing :func:`simulate_trade`
and :func:`calculate_trade_statistics` functions for each window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from src.backtest.metrics import (
    calculate_max_drawdown,
    calculate_trade_statistics,
)
from src.backtest.trade_simulator import simulate_trade
from src.logging.logger import logger
from src.strategies.base import BaseStrategy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_COMMISSION = 0.001
_DEFAULT_SLIPPAGE = 0.005
_DEFAULT_START_INDEX = 30
_FORWARD_BUFFER = 10

_CONSISTENCY_THRESHOLD_ELITE = 90.0
_CONSISTENCY_THRESHOLD_GOOD = 75.0
_CONSISTENCY_THRESHOLD_AVERAGE = 50.0

_PASS_RETURN = 0.0
_PASS_WIN_RATE = 50.0
_PASS_CONSISTENCY = 70.0


# ---------------------------------------------------------------------------
# WalkForwardResult — per-window output dataclass
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardResult:
    """Performance result for a single walk-forward window.

    Attributes:
        window_number:
            1-based index of the window in the walk-forward sequence.
        train_start:
            Index of the first training row (inclusive).
        train_end:
            Index of the last training row (inclusive).
        test_start:
            Index of the first testing row (inclusive).
        test_end:
            Index of the last testing row (inclusive).
        training_return:
            Total portfolio return percentage on the training window.
        testing_return:
            Total portfolio return percentage on the testing window.
        training_win_rate:
            Win rate percentage on the training window (0–100).
        testing_win_rate:
            Win rate percentage on the testing window (0–100).
        training_profit_factor:
            Profit factor on the training window.
        testing_profit_factor:
            Profit factor on the testing window.
        training_drawdown:
            Maximum drawdown percentage on the training window.
        testing_drawdown:
            Maximum drawdown percentage on the testing window.
        passed:
            Whether this window passed the performance criteria.
    """

    window_number: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    training_return: float = 0.0
    testing_return: float = 0.0

    training_win_rate: float = 0.0
    testing_win_rate: float = 0.0

    training_profit_factor: float = 0.0
    testing_profit_factor: float = 0.0

    training_drawdown: float = 0.0
    testing_drawdown: float = 0.0

    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with all performance fields.
        """
        return {
            "window_number": self.window_number,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "training_return": self.training_return,
            "testing_return": self.testing_return,
            "training_win_rate": self.training_win_rate,
            "testing_win_rate": self.testing_win_rate,
            "training_profit_factor": self.training_profit_factor,
            "testing_profit_factor": self.testing_profit_factor,
            "training_drawdown": self.training_drawdown,
            "testing_drawdown": self.testing_drawdown,
            "passed": self.passed,
        }


# ---------------------------------------------------------------------------
# WalkForwardOptimizer
# ---------------------------------------------------------------------------


class WalkForwardOptimizer:
    """Rolling walk-forward analysis for a single trading strategy.

    Splits a DataFrame into consecutive train/test windows and evaluates
    the strategy's performance on each window independently.  The summary
    metrics indicate whether the strategy's performance is consistent
    across time or likely overfit to a specific period.

    Usage::

        from src.strategies.momentum import MomentumStrategy
        from src.optimization.walk_forward import WalkForwardOptimizer

        optimizer = WalkForwardOptimizer(
            train_size=100,
            test_size=20,
        )

        strategy = MomentumStrategy()
        results = optimizer.run(strategy, dataframe, initial_capital=1_000_000)

        summary = optimizer.summary()
        print(summary["passed"])
    """

    def __init__(
        self,
        train_size: int,
        test_size: int,
        step_size: int | None = None,
    ) -> None:
        """Initialise the walk-forward optimiser.

        Args:
            train_size:
                Number of rows in each training window.  Must be > 0.
            test_size:
                Number of rows in each testing window.  Must be > 0.
            step_size:
                Number of rows to advance between consecutive windows.
                Defaults to ``test_size`` (non-overlapping windows).

        Raises:
            ValueError: If any size parameter is not positive.
        """
        if train_size <= 0:
            raise ValueError(
                f"train_size must be positive, got {train_size}."
            )
        if test_size <= 0:
            raise ValueError(
                f"test_size must be positive, got {test_size}."
            )

        if step_size is None:
            step_size = test_size

        if step_size <= 0:
            raise ValueError(
                f"step_size must be positive, got {step_size}."
            )

        self._train_size = train_size
        self._test_size = test_size
        self._step_size = step_size

        self._results: list[WalkForwardResult] = []

        logger.debug(
            "WalkForwardOptimizer(train_size=%d, test_size=%d, step_size=%d)",
            train_size,
            test_size,
            step_size,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def split_dataframe(
        self,
        df: Any,
    ) -> list[dict[str, Any]]:
        """Split a DataFrame into rolling train/test windows.

        Each window contains a training slice and a testing slice, along
        with the index boundaries for both.

        Windows start at row 0 and advance by ``step_size`` rows each
        iteration until the test window no longer fits in the DataFrame.

        Args:
            df:
                A pandas DataFrame with OHLCV price data.  Must have at
                least ``train_size + test_size`` rows.

        Returns:
            A list of dictionaries, each containing:

            - **train**: The training DataFrame slice.
            - **test**: The testing DataFrame slice.
            - **train_start**: Start index of training (inclusive).
            - **train_end**: End index of training (inclusive).
            - **test_start**: Start index of testing (inclusive).
            - **test_end**: End index of testing (inclusive).
        """
        n = len(df)
        windows: list[dict[str, Any]] = []

        if n < self._train_size + self._test_size:
            logger.warning(
                "DataFrame has only %d rows; need at least %d "
                "for even a single window.",
                n,
                self._train_size + self._test_size,
            )
            return windows

        test_start = self._train_size

        while test_start + self._test_size <= n:
            train_start = test_start - self._train_size
            test_end = test_start + self._test_size - 1

            windows.append(
                {
                    "train": df.iloc[train_start:test_start],
                    "test": df.iloc[test_start : test_start + self._test_size],
                    "train_start": train_start,
                    "train_end": test_start - 1,
                    "test_start": test_start,
                    "test_end": test_end,
                }
            )

            test_start += self._step_size

        logger.debug(
            "split_dataframe: generated %d windows from %d rows.",
            len(windows),
            n,
        )

        return windows

    def run(
        self,
        strategy: BaseStrategy,
        dataframe: Any,
        initial_capital: float,
    ) -> list[WalkForwardResult]:
        """Execute a full walk-forward analysis.

        For each window generated by :meth:`split_dataframe`, the
        strategy is backtested on the training slice and the testing
        slice independently.  Trade statistics are computed per window.

        Args:
            strategy:
                An instance of :class:`BaseStrategy` (e.g.
                ``MomentumStrategy``).
            dataframe:
                A pandas DataFrame with OHLCV price data.
            initial_capital:
                Starting portfolio capital in NPR for return calculations.

        Returns:
            A list of :class:`WalkForwardResult` instances, one per
            window, in chronological order.
        """
        self._results = []
        windows = self.split_dataframe(dataframe)

        if not windows:
            logger.warning(
                "No windows generated — check that the DataFrame has "
                "enough rows (need >= %d).",
                self._train_size + self._test_size,
            )
            return []

        for idx, window in enumerate(windows, start=1):
            logger.info(
                "Window %d/%d: train=[%d:%d] test=[%d:%d]",
                idx,
                len(windows),
                window["train_start"],
                window["train_end"],
                window["test_start"],
                window["test_end"],
            )

            try:
                result = self._evaluate_window(
                    window_number=idx,
                    strategy=strategy,
                    train_df=window["train"],
                    test_df=window["test"],
                    initial_capital=initial_capital,
                )
                self._results.append(result)

                logger.info(
                    "Window %d: train_return=%.2f%% "
                    "test_return=%.2f%% passed=%s",
                    idx,
                    result.training_return,
                    result.testing_return,
                    result.passed,
                )

            except Exception as exc:
                logger.exception(
                    "Window %d failed: %s",
                    idx,
                    exc,
                )
                self._results.append(
                    WalkForwardResult(
                        window_number=idx,
                        train_start=window["train_start"],
                        train_end=window["train_end"],
                        test_start=window["test_start"],
                        test_end=window["test_end"],
                        passed=False,
                    )
                )

        logger.info(
            "Walk-forward complete: %d windows, %d passed.",
            len(self._results),
            sum(1 for r in self._results if r.passed),
        )

        return self._results

    def summary(self) -> dict[str, Any]:
        """Compute aggregate summary across all windows.

        Returns:
            A dictionary containing:

            - **windows**: Total number of windows evaluated.
            - **average_training_return**: Mean training return %.
            - **average_testing_return**: Mean testing return %.
            - **average_training_win_rate**: Mean training win rate %.
            - **average_testing_win_rate**: Mean testing win rate %.
            - **best_window**: Window number with highest testing return.
            - **worst_window**: Window number with lowest testing return.
            - **consistency_score**: Ratio of testing to training return
              averaged across windows, clamped to 0–100.
            - **consistency_label**: Qualitative label for the
              consistency score.
            - **passed**: ``True`` if average testing return > 0%,
              average testing win rate > 50%, and consistency > 70%.
        """
        if not self._results:
            return {
                "windows": 0,
                "average_training_return": 0.0,
                "average_testing_return": 0.0,
                "average_training_win_rate": 0.0,
                "average_testing_win_rate": 0.0,
                "best_window": None,
                "worst_window": None,
                "consistency_score": 0.0,
                "consistency_label": "N/A",
                "passed": False,
            }

        train_returns = [r.training_return for r in self._results]
        test_returns = [r.testing_return for r in self._results]
        train_wr = [r.training_win_rate for r in self._results]
        test_wr = [r.testing_win_rate for r in self._results]

        avg_train_return = round(fmean(train_returns), 2)
        avg_test_return = round(fmean(test_returns), 2)
        avg_train_wr = round(fmean(train_wr), 2)
        avg_test_wr = round(fmean(test_wr), 2)

        best_idx = max(
            range(len(test_returns)),
            key=lambda i: test_returns[i],
        )
        worst_idx = min(
            range(len(test_returns)),
            key=lambda i: test_returns[i],
        )

        # Consistency score: mean of (test_return / train_return) per
        # window, clamped to [0, 100].
        ratios: list[float] = []
        for tr, te in zip(train_returns, test_returns):
            if tr > 0:
                ratio = (te / tr) * 100.0
                ratios.append(max(0.0, min(100.0, ratio)))
            elif te > 0:
                ratios.append(100.0)
            else:
                ratios.append(0.0)

        consistency_score = round(
            fmean(ratios) if ratios else 0.0, 2
        )

        consistency_label = self._consistency_label(consistency_score)

        passed = (
            avg_test_return > _PASS_RETURN
            and avg_test_wr > _PASS_WIN_RATE
            and consistency_score > _PASS_CONSISTENCY
        )

        return {
            "windows": len(self._results),
            "average_training_return": avg_train_return,
            "average_testing_return": avg_test_return,
            "average_training_win_rate": avg_train_wr,
            "average_testing_win_rate": avg_test_wr,
            "best_window": self._results[best_idx].window_number,
            "worst_window": self._results[worst_idx].window_number,
            "consistency_score": consistency_score,
            "consistency_label": consistency_label,
            "passed": passed,
        }

    # ------------------------------------------------------------------
    # Internal — per-window evaluation
    # ------------------------------------------------------------------

    def _evaluate_window(
        self,
        window_number: int,
        strategy: BaseStrategy,
        train_df: Any,
        test_df: Any,
        initial_capital: float,
    ) -> WalkForwardResult:
        """Evaluate one train/test window.

        Runs the strategy on both slices independently and computes
        performance metrics for each.

        Args:
            window_number:
                1-based window index.
            strategy:
                Strategy instance to evaluate.
            train_df:
                Training DataFrame slice.
            test_df:
                Testing DataFrame slice.
            initial_capital:
                Starting capital for return % calculations.

        Returns:
            A :class:`WalkForwardResult` for this window.
        """
        train_trades = self._backtest_dataframe(
            df=train_df,
            strategy=strategy,
        )
        test_trades = self._backtest_dataframe(
            df=test_df,
            strategy=strategy,
        )

        train_metrics = self._compute_window_metrics(
            trades=train_trades,
            capital=initial_capital,
        )
        test_metrics = self._compute_window_metrics(
            trades=test_trades,
            capital=initial_capital,
        )

        passed = (
            test_metrics["return_pct"] > _PASS_RETURN
            and test_metrics["win_rate"] > _PASS_WIN_RATE
        )

        return WalkForwardResult(
            window_number=window_number,
            train_start=0,
            train_end=len(train_df) - 1,
            test_start=0,
            test_end=len(test_df) - 1,
            training_return=round(train_metrics["return_pct"], 2),
            testing_return=round(test_metrics["return_pct"], 2),
            training_win_rate=round(train_metrics["win_rate"], 2),
            testing_win_rate=round(test_metrics["win_rate"], 2),
            training_profit_factor=round(train_metrics["profit_factor"], 2),
            testing_profit_factor=round(test_metrics["profit_factor"], 2),
            training_drawdown=round(train_metrics["drawdown"], 2),
            testing_drawdown=round(test_metrics["drawdown"], 2),
            passed=passed,
        )

    # ------------------------------------------------------------------
    # Internal — backtest on DataFrame
    # ------------------------------------------------------------------

    @staticmethod
    def _backtest_dataframe(
        df: Any,
        strategy: BaseStrategy,
    ) -> list[dict[str, Any]]:
        """Run a simplified backtest on a DataFrame using one strategy.

        Iterates through candles, generates signals via the strategy,
        and simulates trades via the engine's ``simulate_trade``.

        Args:
            df:
                OHLCV DataFrame to backtest on.
            strategy:
                Strategy instance to generate signals.

        Returns:
            List of executed trade record dicts.
        """
        trades: list[dict[str, Any]] = []
        n = len(df)

        if n < _DEFAULT_START_INDEX + _FORWARD_BUFFER:
            logger.warning(
                "Window slice has only %d rows; need at least %d "
                "for backtest execution (start_index=%d, buffer=%d).",
                n,
                _DEFAULT_START_INDEX + _FORWARD_BUFFER,
                _DEFAULT_START_INDEX,
                _FORWARD_BUFFER,
            )
            return trades

        end_bound = max(n - _FORWARD_BUFFER, _DEFAULT_START_INDEX)

        for i in range(_DEFAULT_START_INDEX, end_bound):
            history = df.iloc[: i + 1].copy()

            try:
                signal = strategy.generate_signal(history)
            except Exception:
                continue

            if not isinstance(signal, dict):
                continue

            if signal.get("signal") != "BUY":
                continue

            try:
                trade = simulate_trade(
                    df=df,
                    start_index=i,
                    signal=signal,
                    commission=_DEFAULT_COMMISSION,
                    slippage=_DEFAULT_SLIPPAGE,
                )
            except Exception:
                continue

            if trade is not None:
                trades.append(trade)

        return trades

    # ------------------------------------------------------------------
    # Internal — window metrics computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_window_metrics(
        trades: list[dict[str, Any]],
        capital: float,
    ) -> dict[str, float]:
        """Compute performance metrics for a list of trades.

        Args:
            trades:
                List of trade record dicts.
            capital:
                Starting capital for return % calculation.

        Returns:
            Dictionary with ``return_pct``, ``win_rate``,
            ``profit_factor``, and ``drawdown`` keys.
        """
        # Default zeroed metrics
        result: dict[str, float] = {
            "return_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "drawdown": 0.0,
        }

        if not trades:
            return result

        stats = calculate_trade_statistics(trades)
        result["win_rate"] = float(stats.get("win_rate", 0.0))
        result["profit_factor"] = float(stats.get("profit_factor", 0.0))

        # Compute total return from net profits
        net_profits = [
            float(t.get("net_profit", 0.0)) for t in trades
        ]
        total_pnl = sum(net_profits)
        result["return_pct"] = (
            (total_pnl / capital) * 100.0 if capital > 0 else 0.0
        )

        # Compute drawdown from equity curve
        equity = [capital]
        for pnl in net_profits:
            equity.append(equity[-1] + pnl)
        result["drawdown"] = calculate_max_drawdown(equity)

        return result

    # ------------------------------------------------------------------
    # Internal — consistency label
    # ------------------------------------------------------------------

    @staticmethod
    def _consistency_label(score: float) -> str:
        """Map a consistency score to a qualitative label.

        Args:
            score: Consistency score (0–100).

        Returns:
            ``"Excellent"``, ``"Good"``, ``"Average"``, or
            ``"Likely overfit"``.
        """
        if score >= _CONSISTENCY_THRESHOLD_ELITE:
            return "Excellent"
        if score >= _CONSISTENCY_THRESHOLD_GOOD:
            return "Good"
        if score >= _CONSISTENCY_THRESHOLD_AVERAGE:
            return "Average"
        return "Likely overfit"
