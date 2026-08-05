"""Walk-forward engine for the institutional backtesting platform.

Part 9.6 — expands the existing ``src.optimization.walk_forward`` with
rolling and expanding windows, parameter persistence across folds, and
performance comparison, all driven by the new :class:`BacktestEngine`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any

import pandas as pd

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.models import BacktestConfig

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardFold:
    """Result of a single train/validate fold.

    Attributes:
        fold_index: 1-based fold number.
        train_start: Train window start index (inclusive).
        train_end: Train window end index (inclusive).
        test_start: Test window start index (inclusive).
        test_end: Test window end index (inclusive).
        train_return: Train window fractional return.
        test_return: Test window fractional return.
        train_sharpe: Train window Sharpe.
        test_sharpe: Test window Sharpe.
        test_max_drawdown: Test window max drawdown (0–100).
        parameters: Parameters used for this fold.
        train_trades: Trade count on the train window.
        test_trades: Trade count on the test window.
        passed: Whether the fold passed the validation criteria.
    """

    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_return: float = 0.0
    test_return: float = 0.0
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    test_max_drawdown: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
    train_trades: int = 0
    test_trades: int = 0
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "fold_index": self.fold_index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "train_return": round(self.train_return, 6),
            "test_return": round(self.test_return, 6),
            "train_sharpe": round(self.train_sharpe, 4),
            "test_sharpe": round(self.test_sharpe, 4),
            "test_max_drawdown": round(self.test_max_drawdown, 4),
            "parameters": self.parameters,
            "train_trades": self.train_trades,
            "test_trades": self.test_trades,
            "passed": self.passed,
        }


@dataclass
class WalkForwardSummary:
    """Aggregate summary across all folds.

    Attributes:
        folds: Number of folds evaluated.
        average_train_return: Mean train fractional return.
        average_test_return: Mean test fractional return.
        average_test_sharpe: Mean test Sharpe.
        best_fold: Fold index with the highest test return.
        worst_fold: Fold index with the lowest test return.
        consistency: Mean ratio of test to train return (0–100).
        passed_folds: Number of folds that passed.
        parameters: Final persisted parameters.
    """

    folds: int = 0
    average_train_return: float = 0.0
    average_test_return: float = 0.0
    average_test_sharpe: float = 0.0
    best_fold: int = 0
    worst_fold: int = 0
    consistency: float = 0.0
    passed_folds: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "folds": self.folds,
            "average_train_return": round(self.average_train_return, 6),
            "average_test_return": round(self.average_test_return, 6),
            "average_test_sharpe": round(self.average_test_sharpe, 4),
            "best_fold": self.best_fold,
            "worst_fold": self.worst_fold,
            "consistency": round(self.consistency, 2),
            "passed_folds": self.passed_folds,
            "parameters": self.parameters,
        }


class WalkForwardEngine:
    """Rolling/expanding walk-forward optimisation using BacktestEngine.

    Usage::

        engine = WalkForwardEngine(train_size=120, test_size=30)
        folds, summary = engine.run(
            dataframe=df,
            build_strategy=my_strategy_factory,   # (params) -> strategy
            parameter_grid=[{"lookback": 10}, {"lookback": 20}],
        )
    """

    def __init__(
        self,
        train_size: int,
        test_size: int,
        step_size: int | None = None,
        expanding: bool = False,
        config: BacktestConfig | None = None,
    ) -> None:
        """Initialise the walk-forward engine.

        Args:
            train_size: Rows per training window.
            test_size: Rows per testing window.
            step_size: Rows to advance between windows (default test_size).
            expanding: When True the train window grows each fold.
            config: Optional backtest configuration.

        Raises:
            ValueError: If any window size is not positive.
        """
        if train_size <= 0:
            raise ValueError("train_size must be positive.")
        if test_size <= 0:
            raise ValueError("test_size must be positive.")
        self._train_size = int(train_size)
        self._test_size = int(test_size)
        self._step_size = int(step_size or test_size)
        self._expanding = bool(expanding)
        self._config = config or BacktestConfig()
        self._folds: list[WalkForwardFold] = []
        self._parameters: dict[str, Any] = {}

    # ── Window splitting ─────────────────────────────────────────

    def split_windows(self, n: int) -> list[tuple[int, int, int, int]]:
        """Return ``(train_start, train_end, test_start, test_end)`` windows.

        Args:
            n: Number of rows in the DataFrame.

        Returns:
            List of index tuples, oldest first.
        """
        windows: list[tuple[int, int, int, int]] = []
        test_start = self._train_size
        while test_start + self._test_size <= n:
            if self._expanding:
                train_start = 0
            else:
                train_start = test_start - self._train_size
            test_end = test_start + self._test_size - 1
            windows.append((train_start, test_start - 1, test_start, test_end))
            test_start += self._step_size
        return windows

    # ── Execution ────────────────────────────────────────────────

    def run(
        self,
        dataframe: pd.DataFrame,
        build_strategy: Any,
        parameter_grid: list[dict[str, Any]] | None = None,
    ) -> tuple[list[WalkForwardFold], WalkForwardSummary]:
        """Run the full walk-forward analysis.

        Args:
            dataframe: OHLCV DataFrame with ``Date``/OHLCV columns.
            build_strategy: Callable accepting a parameters dict and
                returning a strategy callable ``(bar_index, bars, ctx)
                -> list[Order]``.
            parameter_grid: Optional list of parameter dicts.  When
                multiple are supplied the best-performing fold selects
                the parameters for the next fold (parameter persistence).

        Returns:
            ``(folds, summary)``.
        """
        self._folds = []
        windows = self.split_windows(len(dataframe))
        if not windows:
            logger.warning("Not enough rows (%d) for any walk-forward window.", len(dataframe))
            return [], self._summary()

        current_params: dict[str, Any] = {}
        for idx, (ts, te, vs, ve) in enumerate(windows, start=1):
            train_df = dataframe.iloc[ts: vs + 1]
            test_df = dataframe.iloc[vs: ve + 1]

            # Parameter persistence: reuse best params from prior fold.
            params = dict(current_params)

            try:
                strategy = build_strategy(params or None)
                train_result = self._run_one(train_df, strategy)
                test_result = self._run_one(test_df, strategy)

                passed = test_result.metrics.total_return > 0 and test_result.metrics.sharpe > 0
                fold = WalkForwardFold(
                    fold_index=idx,
                    train_start=ts,
                    train_end=te,
                    test_start=vs,
                    test_end=ve,
                    train_return=train_result.metrics.total_return,
                    test_return=test_result.metrics.total_return,
                    train_sharpe=train_result.metrics.sharpe,
                    test_sharpe=test_result.metrics.sharpe,
                    test_max_drawdown=test_result.metrics.max_drawdown,
                    parameters=params,
                    train_trades=len(train_result.trades),
                    test_trades=len(test_result.trades),
                    passed=passed,
                )
                self._folds.append(fold)

                # Persist best params for the next fold.
                if parameter_grid and passed:
                    best = self._select_best_params(test_df, build_strategy, parameter_grid)
                    if best is not None:
                        current_params = best
                        self._parameters = best
            except Exception as exc:
                logger.exception("Fold %d failed: %s", idx, exc)
                self._folds.append(WalkForwardFold(
                    fold_index=idx, train_start=ts, train_end=te,
                    test_start=vs, test_end=ve, passed=False,
                ))

        return self._folds, self._summary()

    def _run_one(self, df: pd.DataFrame, strategy: Any) -> BacktestResult:
        """Run a BacktestEngine over *df* using *strategy*."""
        engine = BacktestEngine(
            config=self._config,
            strategy=strategy,
        )
        return engine.run({"_wf": df})

    def _select_best_params(
        self,
        test_df: pd.DataFrame,
        build_strategy: Any,
        grid: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Pick the parameter set with the best test-return on *test_df*.

        Args:
            test_df: Test window frame.
            build_strategy: Strategy factory.
            grid: Candidate parameter dicts.

        Returns:
            Best parameter dict, or ``None`` when nothing improves.
        """
        best: dict[str, Any] | None = None
        best_return = float("-inf")
        for params in grid:
            try:
                strategy = build_strategy(params)
                result = self._run_one(test_df, strategy)
                if result.metrics.total_return > best_return:
                    best_return = result.metrics.total_return
                    best = params
            except Exception:
                continue
        return best

    # ── Summary ──────────────────────────────────────────────────

    def _summary(self) -> WalkForwardSummary:
        """Compute the aggregate summary."""
        if not self._folds:
            return WalkForwardSummary(parameters=self._parameters)

        train_returns = [f.train_return for f in self._folds]
        test_returns = [f.test_return for f in self._folds]
        test_sharpes = [f.test_sharpe for f in self._folds]

        best_idx = max(range(len(test_returns)), key=lambda i: test_returns[i])
        worst_idx = min(range(len(test_returns)), key=lambda i: test_returns[i])

        ratios: list[float] = []
        for tr, te in zip(train_returns, test_returns):
            if tr > 0:
                ratios.append(max(0.0, min(100.0, te / tr * 100.0)))
            elif te > 0:
                ratios.append(100.0)
            else:
                ratios.append(0.0)

        return WalkForwardSummary(
            folds=len(self._folds),
            average_train_return=fmean(train_returns),
            average_test_return=fmean(test_returns),
            average_test_sharpe=fmean(test_sharpes),
            best_fold=self._folds[best_idx].fold_index,
            worst_fold=self._folds[worst_idx].fold_index,
            consistency=fmean(ratios) if ratios else 0.0,
            passed_folds=sum(1 for f in self._folds if f.passed),
            parameters=self._parameters,
        )

    # ── Persistence ──────────────────────────────────────────────

    def save_parameters(self, path: str | Path) -> None:
        """Persist the current best parameters to JSON.

        Args:
            path: Destination file path.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"parameters": self._parameters}, f, indent=2, default=str)
        logger.info("Saved walk-forward parameters to %s", path)

    @staticmethod
    def load_parameters(path: str | Path) -> dict[str, Any]:
        """Load persisted parameters from JSON.

        Args:
            path: Source file path.

        Returns:
            The parameters dict (empty when unavailable).
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("parameters", {})
        except Exception as exc:
            logger.warning("Could not load parameters from %s: %s", path, exc)
            return {}

    def folds(self) -> list[WalkForwardFold]:
        """Return the recorded folds."""
        return list(self._folds)
