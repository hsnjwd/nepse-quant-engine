"""Parameter Optimisation Engine for the NEPSE Quant Engine.

Automatically searches for the best parameter combinations for any
registered strategy using grid search or random search, with the
architecture designed so that Bayesian optimisation can be added later.

The optimizer temporarily injects parameters into a strategy instance,
runs a full backtest, collects performance metrics via the
:class:`~src.analytics.performance.PerformanceAnalyzer`, restores the
original parameters, and then ranks the results by the chosen metric.
"""

from __future__ import annotations

import csv
import itertools
import math
import random
import time
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from src.analytics.performance import PerformanceAnalyzer
from src.backtest.metrics import calculate_trade_statistics
from src.backtest.trade_simulator import simulate_trade
from src.logging.logger import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_COMMISSION = 0.001
_DEFAULT_SLIPPAGE = 0.005
_DEFAULT_START_INDEX = 30
_FORWARD_BUFFER = 10

_VALID_METRICS = frozenset(
    {
        "net_profit",
        "return_pct",
        "profit_factor",
        "win_rate",
        "expectancy",
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "max_drawdown",
        "recovery_factor",
        "score",
    }
)


# ---------------------------------------------------------------------------
# ParameterResult — single-parameter-set output
# ---------------------------------------------------------------------------


@dataclass
class ParameterResult:
    """Performance for a single parameter combination.

    Attributes:
        parameters:
            The parameter dict that was evaluated.
        net_profit:
            Net profit in NPR.
        return_pct:
            Total portfolio return percentage.
        win_rate:
            Win rate percentage (0–100).
        profit_factor:
            Ratio of gross profit to gross loss.
        drawdown:
            Maximum drawdown percentage.
        score:
            Composite quality score (0–100).
        metrics:
            Full metrics dict from the performance analyser (includes
            all StrategyPerformance fields).
    """

    parameters: dict[str, Any]
    net_profit: float = 0.0
    return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    drawdown: float = 0.0
    score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "parameters": dict(self.parameters),
            "net_profit": self.net_profit,
            "return_pct": self.return_pct,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "drawdown": self.drawdown,
            "score": self.score,
            "metrics": dict(self.metrics),
        }


# ---------------------------------------------------------------------------
# OptimizationResult — full-optimisation output
# ---------------------------------------------------------------------------


@dataclass
class OptimizationResult:
    """Complete result of a parameter optimisation run.

    Attributes:
        strategy_name:
            Name of the strategy that was optimised.
        best_parameters:
            The parameter dict that achieved the best metric value.
        best_metric:
            The numeric value of the chosen metric for the best set.
        metric_name:
            The metric that was used for ranking (e.g. ``"net_profit"``).
        results:
            All :class:`ParameterResult` instances, one per tested
            combination, sorted by the chosen metric descending.
        total_combinations:
            Total number of possible combinations (may exceed tested).
        tested_combinations:
            Number of combinations actually evaluated.
        runtime_seconds:
            Wall-clock duration of the optimisation run.
        ranking:
            Abbreviated ranking of the top results.
    """

    strategy_name: str
    best_parameters: dict[str, Any] = field(default_factory=dict)
    best_metric: float = 0.0
    metric_name: str = "net_profit"
    results: list[ParameterResult] = field(default_factory=list)
    total_combinations: int = 0
    tested_combinations: int = 0
    runtime_seconds: float = 0.0
    ranking: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a fully serialisable dictionary representation."""
        return {
            "strategy_name": self.strategy_name,
            "best_parameters": dict(self.best_parameters),
            "best_metric": self.best_metric,
            "metric_name": self.metric_name,
            "results": [r.to_dict() for r in self.results],
            "total_combinations": self.total_combinations,
            "tested_combinations": self.tested_combinations,
            "runtime_seconds": round(self.runtime_seconds, 2),
            "ranking": list(self.ranking),
        }


# ---------------------------------------------------------------------------
# ParameterOptimizer
# ---------------------------------------------------------------------------


class ParameterOptimizer:
    """Search for optimal strategy parameters.

    Supports grid search (exhaustive over a cartesian product of discrete
    values) and random search (sampling from integer/float ranges or
    discrete lists).  Designed so that Bayesian optimisation can be added
    as a third search method without changing the public API.

    The optimizer never permanently modifies a strategy — it captures,
    temporarily injects, and restores the original parameters after each
    evaluation.

    Usage::

        from src.strategies.momentum import MomentumStrategy
        from src.optimization.parameter_optimizer import (
            ParameterOptimizer,
        )

        strategy = MomentumStrategy()
        optimizer = ParameterOptimizer(strategy)

        # Grid search
        result = optimizer.optimize_grid(
            historical_data=df,
            parameter_grid={
                "rsi_period": [10, 14, 20],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="score",
        )

        # Random search
        result = optimizer.optimize_random(
            historical_data=df,
            parameter_ranges={
                "rsi_period": (8, 25),
                "stop_loss_pct": (0.01, 0.10),
            },
            iterations=50,
            initial_capital=1_000_000,
        )
    """

    def __init__(
        self,
        strategy: Any,
        backtest_engine: Any = None,
        performance_analyzer: Any = None,
    ) -> None:
        """Initialise the parameter optimiser.

        Args:
            strategy:
                An instance of a ``BaseStrategy`` subclass (e.g.
                ``MomentumStrategy``).  The strategy's public attributes
                will be captured for temporary parameter injection.
            backtest_engine:
                Reserved for future use.  Currently ignored — the
                optimiser runs its own internal backtest loop that
                calls ``strategy.generate_signal()`` directly.
            performance_analyzer:
                Optional :class:`~src.analytics.performance.PerformanceAnalyzer`
                instance.  A default one is created if not provided.
        """
        self._strategy = strategy
        self._strategy_name = self._extract_strategy_name(strategy)
        self._original_params: dict[str, Any] = self._capture_params(strategy)
        self._analyzer = performance_analyzer or PerformanceAnalyzer()
        self._backtest_engine = backtest_engine
        self._results: list[ParameterResult] = []
        self._start_time: float | None = None

        logger.debug(
            "ParameterOptimizer(strategy=%s, params=%d)",
            self._strategy_name,
            len(self._original_params),
        )

    # ------------------------------------------------------------------
    # Public API — grid search
    # ------------------------------------------------------------------

    def optimize_grid(
        self,
        historical_data: Any,
        parameter_grid: dict[str, list[Any]],
        initial_capital: float,
        metric: str = "net_profit",
    ) -> OptimizationResult:
        """Exhaustively evaluate all combinations in a parameter grid.

        The grid is a dict mapping parameter names to lists of discrete
        values.  All combinations are generated via cartesian product.

        Args:
            historical_data:
                A pandas DataFrame with OHLCV price data.
            parameter_grid:
                Dict of ``{param_name: [value1, value2, ...]}``.
            initial_capital:
                Starting portfolio capital in NPR.
            metric:
                The metric to optimise for.  Must be one of:
                ``net_profit``, ``return_pct``, ``profit_factor``,
                ``win_rate``, ``expectancy``, ``sharpe_ratio``,
                ``sortino_ratio``, ``calmar_ratio``, ``max_drawdown``,
                ``recovery_factor``, ``score``.

        Returns:
            An :class:`OptimizationResult` with all evaluated
            combinations ranked by the chosen metric.

        Raises:
            ValueError: If the grid is empty or the metric is invalid.
        """
        self._validate_metric(metric)
        self._validate_grid(parameter_grid)

        keys = list(parameter_grid.keys())
        value_lists = [parameter_grid[k] for k in keys]
        total = _product_size(value_lists)

        logger.info(
            "Grid search started: strategy=%s metric=%s "
            "combinations=%d",
            self._strategy_name,
            metric,
            total,
        )

        self._start_time = time.perf_counter()
        self._results = []

        for idx, values in enumerate(itertools.product(*value_lists), start=1):
            params = dict(zip(keys, values))
            logger.debug(
                "Combination %d/%d: %s",
                idx,
                total,
                params,
            )
            result = self._evaluate_combination(
                parameters=params,
                historical_data=historical_data,
                initial_capital=initial_capital,
            )
            self._results.append(result)

        runtime = time.perf_counter() - (self._start_time or 0.0)

        return self._build_result(
            metric=metric,
            total=total,
            tested=len(self._results),
            runtime=runtime,
        )

    # ------------------------------------------------------------------
    # Public API — random search
    # ------------------------------------------------------------------

    def optimize_random(
        self,
        historical_data: Any,
        parameter_ranges: dict[str, Any],
        iterations: int,
        initial_capital: float,
        metric: str = "net_profit",
        random_seed: int | None = None,
    ) -> OptimizationResult:
        """Randomly sample parameter combinations.

        Supports three range types:

        - ``(low, high)`` tuple — sampled as **integer** when both
          bounds are ``int``, **float** otherwise.
        - ``[v1, v2, ...]`` list — sampled uniformly from the list.
        - A single value — used as-is for every iteration.

        Args:
            historical_data:
                A pandas DataFrame with OHLCV price data.
            parameter_ranges:
                Dict of ``{param_name: range_spec}`` where
                ``range_spec`` is a ``(low, high)`` tuple, a list of
                discrete values, or a scalar.
            iterations:
                Number of random combinations to evaluate.
            initial_capital:
                Starting portfolio capital in NPR.
            metric:
                The metric to optimise for.
            random_seed:
                Optional seed for reproducible random sampling.

        Returns:
            An :class:`OptimizationResult` with the sampled combinations
            ranked by the chosen metric.

        Raises:
            ValueError: If the ranges are empty or the metric is invalid.
        """
        self._validate_metric(metric)
        if not parameter_ranges:
            raise ValueError("Parameter ranges must not be empty.")
        if iterations <= 0:
            raise ValueError(
                f"iterations must be positive, got {iterations}."
            )

        if random_seed is not None:
            random.seed(random_seed)

        logger.info(
            "Random search started: strategy=%s metric=%s "
            "iterations=%d seed=%s",
            self._strategy_name,
            metric,
            iterations,
            str(random_seed),
        )

        self._start_time = time.perf_counter()
        self._results = []

        for idx in range(1, iterations + 1):
            params = self._sample_random_parameters(parameter_ranges)
            logger.debug(
                "Iteration %d/%d: %s",
                idx,
                iterations,
                params,
            )
            result = self._evaluate_combination(
                parameters=params,
                historical_data=historical_data,
                initial_capital=initial_capital,
            )
            self._results.append(result)

        runtime = time.perf_counter() - (self._start_time or 0.0)

        return self._build_result(
            metric=metric,
            total=0,
            tested=len(self._results),
            runtime=runtime,
        )

    # ------------------------------------------------------------------
    # Public API — evaluate & rank
    # ------------------------------------------------------------------

    def evaluate_parameters(
        self,
        parameters: dict[str, Any],
        historical_data: Any,
        initial_capital: float,
    ) -> dict[str, Any]:
        """Evaluate a single parameter combination.

        Injects the parameters, runs the strategy, backtests,
        analyses performance, restores original parameters, and returns
        the full metrics.

        Args:
            parameters:
                Dict of parameter values to inject.
            historical_data:
                OHLCV DataFrame.
            initial_capital:
                Starting capital in NPR.

        Returns:
            A ``StrategyPerformance.to_dict()`` dictionary with
            performance metrics.
        """
        result = self._evaluate_combination(
            parameters=parameters,
            historical_data=historical_data,
            initial_capital=initial_capital,
        )
        return result.metrics

    def rank_results(
        self,
        metric: str = "net_profit",
    ) -> list[ParameterResult]:
        """Sort all evaluated results by a chosen metric descending.

        Args:
            metric:
                Metric to sort by.  Defaults to ``"net_profit"``.

        Returns:
            A sorted copy of the results list.
        """
        self._validate_metric(metric)
        return _sort_results(self._results, metric)

    # ------------------------------------------------------------------
    # Public API — summary & export
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary of the last optimisation run.

        Returns:
            Dictionary with the best parameter set, top 10
            combinations, average metric, worst combination, and
            runtime.
        """
        if not self._results:
            return {
                "best_parameters": {},
                "top_10": [],
                "average_metric": 0.0,
                "worst_parameters": {},
                "worst_metric": 0.0,
                "total_evaluated": 0,
                "runtime_seconds": 0.0,
            }

        sorted_results = _sort_results(self._results, "score")
        best = sorted_results[0]
        worst = sorted_results[-1]
        scores = [r.score for r in self._results]

        return {
            "best_parameters": dict(best.parameters),
            "best_score": best.score,
            "best_net_profit": best.net_profit,
            "top_10": [r.to_dict() for r in sorted_results[:10]],
            "average_score": round(fmean(scores), 2),
            "average_net_profit": round(
                fmean([r.net_profit for r in self._results]), 2
            ),
            "worst_parameters": dict(worst.parameters),
            "worst_score": worst.score,
            "total_evaluated": len(self._results),
            "runtime_seconds": round(
                time.perf_counter() - (self._start_time or 0.0), 2
            ),
        }

    def export_csv(self, path: str) -> None:
        """Export all optimisation results to a CSV file.

        Each row represents one evaluated parameter combination with
        its performance metrics.  Parameter columns are written first,
        followed by the metric columns.

        Args:
            path:
                File path for the CSV output.

        Raises:
            OSError: If the file cannot be written.
        """
        if not self._results:
            logger.warning("No results to export — CSV will be empty.")

        param_keys = self._get_all_param_keys(self._results)
        metric_keys = [
            "net_profit",
            "return_pct",
            "win_rate",
            "profit_factor",
            "drawdown",
            "score",
        ]
        fieldnames = param_keys + metric_keys

        with open(path, mode="w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self._results:
                row = dict(r.parameters)
                row["net_profit"] = r.net_profit
                row["return_pct"] = r.return_pct
                row["win_rate"] = r.win_rate
                row["profit_factor"] = r.profit_factor
                row["drawdown"] = r.drawdown
                row["score"] = r.score
                writer.writerow(row)

        logger.info("Exported %d results to %s", len(self._results), path)

    def to_dict(self) -> dict[str, Any]:
        """Return the last optimisation result as a serialisable dict.

        Returns:
            A dictionary with all results, best parameters, and
            runtime.
        """
        sorted_results = _sort_results(self._results, "score")
        return {
            "strategy_name": self._strategy_name,
            "results": [r.to_dict() for r in sorted_results],
            "total_evaluated": len(self._results),
            "runtime_seconds": round(
                time.perf_counter() - (self._start_time or 0.0), 2
            ),
        }

    # ------------------------------------------------------------------
    # Internal — parameter capture / injection / restore
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_strategy_name(strategy: Any) -> str:
        """Return the strategy's name."""
        if hasattr(strategy, "name"):
            return strategy.name
        return strategy.__class__.__name__

    @staticmethod
    def _capture_params(strategy: Any) -> dict[str, Any]:
        """Capture all public, writeable attributes of a strategy.

        Skips dunder attributes, private attributes (prefixed with
        ``_``), read-only properties (e.g. ``name``, ``description``,
        ``version`` from :class:`BaseStrategy`), and callables.
        """
        params: dict[str, Any] = {}
        cls = type(strategy)
        for attr_name in dir(strategy):
            if attr_name.startswith("_"):
                continue
            # Skip read-only properties — they have no setter.
            if isinstance(getattr(cls, attr_name, None), property):
                continue
            try:
                value = getattr(strategy, attr_name)
            except Exception:
                continue
            if callable(value):
                continue
            params[attr_name] = value
        return params

    def _inject_params(self, parameters: dict[str, Any]) -> None:
        """Temporarily set strategy attributes from a parameter dict."""
        for key, value in parameters.items():
            if hasattr(self._strategy, key):
                setattr(self._strategy, key, value)

    def _restore_params(self) -> None:
        """Restore the strategy's original parameters."""
        for key, value in self._original_params.items():
            if hasattr(self._strategy, key):
                setattr(self._strategy, key, value)

    # ------------------------------------------------------------------
    # Internal — evaluation pipeline
    # ------------------------------------------------------------------

    def _evaluate_combination(
        self,
        parameters: dict[str, Any],
        historical_data: Any,
        initial_capital: float,
    ) -> ParameterResult:
        """Inject, backtest, analyse, restore for one parameter set."""
        self._inject_params(parameters)

        try:
            trades = self._backtest_strategy(
                df=historical_data,
            )
            perf = self._analyzer.analyze(
                strategy_name=self._strategy_name,
                symbol="OPTIMIZATION",
                starting_capital=initial_capital,
                trade_history=trades,
            )

            return ParameterResult(
                parameters=dict(parameters),
                net_profit=perf.net_profit,
                return_pct=perf.return_pct,
                win_rate=perf.win_rate,
                profit_factor=perf.profit_factor,
                drawdown=perf.max_drawdown,
                score=perf.score,
                metrics=perf.to_dict(),
            )
        except Exception as exc:
            logger.warning(
                "Combination %s failed: %s",
                parameters,
                exc,
            )
            return ParameterResult(
                parameters=dict(parameters),
            )
        finally:
            self._restore_params()

    # ------------------------------------------------------------------
    # Internal — backtest on a DataFrame using the strategy
    # ------------------------------------------------------------------

    def _backtest_strategy(
        self,
        df: Any,
    ) -> list[dict[str, Any]]:
        """Run a simplified backtest using the strategy's signal generator.

        Iterates through candles, calls ``strategy.generate_signal()``
        on each history slice, and simulates BUY trades via
        ``simulate_trade()``.

        Args:
            df:
                OHLCV DataFrame to backtest.

        Returns:
            List of trade record dicts.
        """
        trades: list[dict[str, Any]] = []
        n = len(df)

        if n < _DEFAULT_START_INDEX + _FORWARD_BUFFER:
            logger.warning(
                "DataFrame has only %d rows; need at least %d "
                "for backtest execution.",
                n,
                _DEFAULT_START_INDEX + _FORWARD_BUFFER,
            )
            return trades

        end_bound = max(n - _FORWARD_BUFFER, _DEFAULT_START_INDEX)

        for i in range(_DEFAULT_START_INDEX, end_bound):
            history = df.iloc[: i + 1].copy()

            try:
                signal = self._strategy.generate_signal(history)
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
    # Internal — random sampling
    # ------------------------------------------------------------------

    @staticmethod
    def _sample_random_parameters(
        ranges: dict[str, Any],
    ) -> dict[str, Any]:
        """Sample one random parameter combination.

        For each param in *ranges*:

        - ``(low, high)`` with int bounds → random integer.
        - ``(low, high)`` with float bounds → random float.
        - ``[...]`` list → uniform choice from the list.
        - Scalar → used as-is.
        """
        params: dict[str, Any] = {}
        for key, spec in ranges.items():
            if isinstance(spec, (list, tuple)) and len(spec) == 2:
                low, high = spec
                if isinstance(low, int) and isinstance(high, int):
                    params[key] = random.randint(low, high)
                else:
                    params[key] = random.uniform(float(low), float(high))
            elif isinstance(spec, list):
                params[key] = random.choice(spec)
            else:
                params[key] = spec
        return params

    # ------------------------------------------------------------------
    # Internal — result building
    # ------------------------------------------------------------------

    def _build_result(
        self,
        metric: str,
        total: int,
        tested: int,
        runtime: float,
    ) -> OptimizationResult:
        """Build an OptimizationResult from the internal results list."""
        sorted_results = _sort_results(self._results, metric)
        best = sorted_results[0] if sorted_results else ParameterResult(parameters={})
        best_metric = self._extract_metric_value(best, metric)

        # Build abbreviated ranking (param name + metric value)
        ranking: list[dict[str, Any]] = []
        for idx, r in enumerate(sorted_results[:20], start=1):
            ranking.append(
                {
                    "rank": idx,
                    "parameters": dict(r.parameters),
                    metric: self._extract_metric_value(r, metric),
                    "score": r.score,
                }
            )

        logger.info(
            "Optimisation complete: strategy=%s metric=%s "
            "tested=%d best=%.4f runtime=%.2fs",
            self._strategy_name,
            metric,
            tested,
            best_metric,
            runtime,
        )

        return OptimizationResult(
            strategy_name=self._strategy_name,
            best_parameters=dict(best.parameters),
            best_metric=best_metric,
            metric_name=metric,
            results=sorted_results,
            total_combinations=total,
            tested_combinations=tested,
            runtime_seconds=runtime,
            ranking=ranking,
        )

    # ------------------------------------------------------------------
    # Internal — validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_metric(metric: str) -> None:
        """Raise ValueError if *metric* is not recognised."""
        if metric not in _VALID_METRICS:
            raise ValueError(
                f"Unknown metric '{metric}'. "
                f"Valid metrics: {sorted(_VALID_METRICS)}"
            )

    @staticmethod
    def _validate_grid(grid: dict[str, list[Any]]) -> None:
        """Raise ValueError if *grid* is empty."""
        if not grid:
            raise ValueError(
                "Parameter grid must not be empty."
            )
        for key, values in grid.items():
            if not isinstance(values, list):
                raise ValueError(
                    f"Grid value for '{key}' must be a list, "
                    f"got {type(values).__name__}."
                )
            if not values:
                raise ValueError(
                    f"Grid value list for '{key}' must not be empty."
                )

    # ------------------------------------------------------------------
    # Internal — metric extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_metric_value(
        result: ParameterResult,
        metric: str,
    ) -> float:
        """Extract a specific metric from a ParameterResult.

        Handles ``None`` values (e.g. sharpe_ratio can be ``None`` for
        single-trade results) by returning ``0.0``.
        """
        value = getattr(result, metric, None)
        if value is None:
            return 0.0
        return float(value)

    @staticmethod
    def _get_all_param_keys(
        results: list[ParameterResult] | None = None,
    ) -> list[str]:
        """Return all unique parameter keys across results.

        If *results* is ``None``, returns keys from the original
        captured params (avoids needing results for CSV header).
        """
        if results is None:
            return []
        keys: set[str] = set()
        for r in results:
            keys.update(r.parameters.keys())
        return sorted(keys)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _product_size(lists: list[list[Any]]) -> int:
    """Return the product of list lengths, defaulting to 0 for empty."""
    if not lists:
        return 0
    size = 1
    for lst in lists:
        size *= len(lst)
    return size


def _sort_results(
    results: list[ParameterResult],
    metric: str,
) -> list[ParameterResult]:
    """Sort results by a metric field descending (None values last)."""
    def key_fn(r: ParameterResult) -> float:
        val = getattr(r, metric, None)
        if val is None:
            return float("-inf")
        return float(val)

    return sorted(results, key=key_fn, reverse=True)
