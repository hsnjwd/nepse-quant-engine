"""Tests for the Parameter Optimisation Engine.

Covers constructor validation, grid search, random search, parameter
injection, ranking, CSV/JSON export, metric selection, and error
handling.
"""

from __future__ import annotations

import csv
import math
import os
import tempfile

import pandas as pd
import pytest

from src.optimization.parameter_optimizer import (
    OptimizationResult,
    ParameterOptimizer,
    ParameterResult,
)


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def sample_dataframe() -> pd.DataFrame:
    """Create a 200-row OHLCV DataFrame for testing."""
    dates = pd.date_range("2024-01-01", periods=200)
    data = []
    price = 100.0
    for i, dt in enumerate(dates):
        price += 1.0 if i % 3 == 0 else -0.3
        data.append(
            {
                "Date": dt,
                "Open": price - 0.5,
                "High": price + 2.0,
                "Low": price - 1.0,
                "Close": price,
                "Volume": 1000 + i * 10,
            }
        )
    return pd.DataFrame(data).set_index("Date")


@pytest.fixture
def momentum_optimizer() -> ParameterOptimizer:
    """A ParameterOptimizer with MomentumStrategy."""
    from src.strategies.momentum import MomentumStrategy

    strategy = MomentumStrategy()
    return ParameterOptimizer(strategy)


@pytest.fixture
def breakout_optimizer() -> ParameterOptimizer:
    """A ParameterOptimizer with BreakoutStrategy."""
    from src.strategies.breakout import BreakoutStrategy

    strategy = BreakoutStrategy()
    return ParameterOptimizer(strategy)


# ======================================================================
# ParameterResult dataclass
# ======================================================================


class TestParameterResult:
    """ParameterResult dataclass serialisation."""

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all expected fields."""
        result = ParameterResult(
            parameters={"rsi_period": 14, "stop_loss_pct": 0.05},
            net_profit=50_000.0,
            return_pct=5.0,
            win_rate=65.0,
            profit_factor=2.5,
            drawdown=4.2,
            score=78.5,
            metrics={"score": 78.5, "net_profit": 50_000.0},
        )
        d = result.to_dict()

        assert d["parameters"] == {"rsi_period": 14, "stop_loss_pct": 0.05}
        assert d["net_profit"] == 50_000.0
        assert d["return_pct"] == 5.0
        assert d["win_rate"] == 65.0
        assert d["profit_factor"] == 2.5
        assert d["drawdown"] == 4.2
        assert d["score"] == 78.5
        assert d["metrics"]["score"] == 78.5
        assert len(d) == 8

    def test_default_values(self) -> None:
        """Default-constructed result has zeroed metrics."""
        result = ParameterResult(parameters={"p": 1})
        assert result.net_profit == 0.0
        assert result.score == 0.0
        assert result.metrics == {}


# ======================================================================
# OptimizationResult dataclass
# ======================================================================


class TestOptimizationResult:
    """OptimizationResult dataclass serialisation."""

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all expected fields."""
        result = OptimizationResult(
            strategy_name="MomentumStrategy",
            best_parameters={"rsi_period": 20},
            best_metric=85_000.0,
            metric_name="net_profit",
            results=[
                ParameterResult(
                    parameters={"rsi_period": 20},
                    net_profit=85_000.0,
                ),
            ],
            total_combinations=10,
            tested_combinations=10,
            runtime_seconds=12.5,
            ranking=[{"rank": 1, "parameters": {"rsi_period": 20}, "net_profit": 85_000.0}],
        )
        d = result.to_dict()

        assert d["strategy_name"] == "MomentumStrategy"
        assert d["best_parameters"] == {"rsi_period": 20}
        assert d["best_metric"] == 85_000.0
        assert d["metric_name"] == "net_profit"
        assert len(d["results"]) == 1
        assert d["total_combinations"] == 10
        assert d["tested_combinations"] == 10
        assert isinstance(d["runtime_seconds"], float)
        assert len(d["ranking"]) == 1


# ======================================================================
# Constructor
# ======================================================================


class TestConstructor:
    """ParameterOptimizer constructor behaviour."""

    def test_captures_strategy_params(self, momentum_optimizer: ParameterOptimizer) -> None:
        """Constructor captures public strategy attributes."""
        assert len(momentum_optimizer._original_params) > 0
        assert "rsi_period" in momentum_optimizer._original_params
        assert "stop_loss_pct" in momentum_optimizer._original_params

    def test_stores_strategy_name(self, momentum_optimizer: ParameterOptimizer) -> None:
        """Strategy name is extracted."""
        assert momentum_optimizer._strategy_name == "MomentumStrategy"

    def test_default_analyzer_created(self, momentum_optimizer: ParameterOptimizer) -> None:
        """Default PerformanceAnalyzer is created."""
        from src.analytics.performance import PerformanceAnalyzer

        assert isinstance(momentum_optimizer._analyzer, PerformanceAnalyzer)

    def test_breakout_params_captured(self, breakout_optimizer: ParameterOptimizer) -> None:
        """BreakoutStrategy params are also captured."""
        assert "lookback_period" in breakout_optimizer._original_params
        assert "volume_threshold" in breakout_optimizer._original_params


# ======================================================================
# Parameter injection and restore
# ======================================================================


class TestParameterInjection:
    """Parameter injection / restore logic."""

    def test_inject_params_changes_strategy(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Injecting params changes strategy attributes."""
        from src.strategies.momentum import MomentumStrategy

        assert momentum_optimizer._strategy.rsi_period == 14
        momentum_optimizer._inject_params({"rsi_period": 20})
        assert momentum_optimizer._strategy.rsi_period == 20

    def test_restore_params_returns_originals(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Restoring params reverts to original values."""
        momentum_optimizer._inject_params({"rsi_period": 99})
        momentum_optimizer._restore_params()
        assert momentum_optimizer._strategy.rsi_period == 14

    def test_inject_only_known_params(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Unknown params are silently ignored."""
        momentum_optimizer._inject_params(
            {"non_existent_param": 999}
        )
        # Should not raise
        momentum_optimizer._restore_params()


# ======================================================================
# Grid search
# ======================================================================


class TestGridSearch:
    """ParameterOptimizer.optimize_grid behaviour."""

    def test_grid_search_returns_optimization_result(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Grid search returns an OptimizationResult."""
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="net_profit",
        )
        assert isinstance(result, OptimizationResult)
        assert len(result.results) > 0

    def test_grid_search_evaluates_all_combinations(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Grid search covers the full cartesian product."""
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14, 20],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="score",
        )
        # 3 × 2 = 6 combinations
        assert result.tested_combinations == 6
        assert result.total_combinations == 6

    def test_grid_search_identifies_best(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Grid search finds the best parameter combination."""
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="score",
        )
        assert result.best_metric > 0 or result.best_metric == 0.0
        assert isinstance(result.best_parameters, dict)

    def test_grid_search_runtime_positive(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Runtime is a positive float."""
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10],
                "stop_loss_pct": [0.03],
            },
            initial_capital=1_000_000,
            metric="profit_factor",
        )
        assert result.runtime_seconds > 0

    def test_grid_search_ranking_populated(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Grid search populates the ranking field."""
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14, 20],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="net_profit",
        )
        assert len(result.ranking) > 0
        assert "rank" in result.ranking[0]


# ======================================================================
# Random search
# ======================================================================


class TestRandomSearch:
    """ParameterOptimizer.optimize_random behaviour."""

    def test_random_search_returns_optimization_result(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Random search returns an OptimizationResult."""
        result = momentum_optimizer.optimize_random(
            historical_data=sample_dataframe,
            parameter_ranges={
                "rsi_period": (10, 20),
                "stop_loss_pct": (0.02, 0.06),
            },
            iterations=5,
            initial_capital=1_000_000,
            random_seed=42,
        )
        assert isinstance(result, OptimizationResult)
        assert len(result.results) == 5

    def test_random_search_integer_range(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Integer ranges produce integer parameter values."""
        params = momentum_optimizer._sample_random_parameters(
            {"rsi_period": (10, 20)}
        )
        assert isinstance(params["rsi_period"], int)
        assert 10 <= params["rsi_period"] <= 20

    def test_random_search_float_range(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Float ranges produce float parameter values."""
        params = momentum_optimizer._sample_random_parameters(
            {"stop_loss_pct": (0.01, 0.10)}
        )
        assert isinstance(params["stop_loss_pct"], float)
        assert 0.01 <= params["stop_loss_pct"] <= 0.10

    def test_random_search_list_spec(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """List specs choose uniformly from the list."""
        params = momentum_optimizer._sample_random_parameters(
            {"ema_fast": [8, 10, 12, 15]}
        )
        assert params["ema_fast"] in [8, 10, 12, 15]

    def test_random_search_scalar_spec(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Scalar specs are used as-is."""
        params = momentum_optimizer._sample_random_parameters(
            {"fixed_param": 42}
        )
        assert params["fixed_param"] == 42

    def test_random_search_reproducible_seed(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Same seed produces same results."""
        result_a = momentum_optimizer.optimize_random(
            historical_data=pd.DataFrame(
                {"Close": range(100), "Open": range(100),
                 "High": range(100), "Low": range(100),
                 "Volume": range(100)}
            ),
            parameter_ranges={"rsi_period": (10, 20)},
            iterations=3,
            initial_capital=1_000_000,
            random_seed=123,
            metric="net_profit",
        )
        result_b = momentum_optimizer.optimize_random(
            historical_data=pd.DataFrame(
                {"Close": range(100), "Open": range(100),
                 "High": range(100), "Low": range(100),
                 "Volume": range(100)}
            ),
            parameter_ranges={"rsi_period": (10, 20)},
            iterations=3,
            initial_capital=1_000_000,
            random_seed=123,
            metric="net_profit",
        )
        assert result_a.best_parameters == result_b.best_parameters

    def test_random_search_with_mixed_ranges(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Mixed range types work together."""
        result = momentum_optimizer.optimize_random(
            historical_data=sample_dataframe,
            parameter_ranges={
                "rsi_period": (10, 20),
                "stop_loss_pct": (0.01, 0.10),
                "rsi_buy_threshold": [50.0, 55.0, 60.0],
            },
            iterations=3,
            initial_capital=1_000_000,
            random_seed=42,
        )
        assert result.tested_combinations == 3


# ======================================================================
# Metric validation
# ======================================================================


class TestMetricValidation:
    """Metric validation logic."""

    def test_valid_metrics_accepted(self, momentum_optimizer: ParameterOptimizer) -> None:
        """All valid metrics are accepted."""
        result = momentum_optimizer.optimize_grid(
            historical_data=pd.DataFrame(
                {"Close": range(100), "Open": range(100),
                 "High": range(100), "Low": range(100),
                 "Volume": range(100)}
            ),
            parameter_grid={"rsi_period": [10]},
            initial_capital=1_000_000,
            metric="win_rate",
        )
        assert isinstance(result, OptimizationResult)

    def test_invalid_metric_raises_value_error(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Invalid metric raises ValueError."""
        with pytest.raises(ValueError, match="Unknown metric"):
            momentum_optimizer.optimize_grid(
                historical_data=pd.DataFrame(
                    {"Close": range(100), "Open": range(100),
                     "High": range(100), "Low": range(100),
                     "Volume": range(100)}
                ),
                parameter_grid={"rsi_period": [10]},
                initial_capital=1_000_000,
                metric="invalid_metric",
            )


# ======================================================================
# Parameter grid validation
# ======================================================================


class TestGridValidation:
    """Parameter grid validation."""

    def test_empty_grid_raises_value_error(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Empty grid raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            momentum_optimizer.optimize_grid(
                historical_data=pd.DataFrame(),
                parameter_grid={},
                initial_capital=1_000_000,
            )

    def test_non_list_grid_value_raises(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Grid values must be lists."""
        with pytest.raises(ValueError, match="must be a list"):
            momentum_optimizer.optimize_grid(
                historical_data=pd.DataFrame(),
                parameter_grid={"rsi_period": 14},
                initial_capital=1_000_000,
            )

    def test_empty_list_in_grid_raises(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Empty list in grid raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            momentum_optimizer.optimize_grid(
                historical_data=pd.DataFrame(),
                parameter_grid={"rsi_period": []},
                initial_capital=1_000_000,
            )

    def test_empty_ranges_raises(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Empty parameter_ranges raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            momentum_optimizer.optimize_random(
                historical_data=pd.DataFrame(),
                parameter_ranges={},
                iterations=10,
                initial_capital=1_000_000,
            )

    def test_negative_iterations_raises(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Negative iterations raises ValueError."""
        with pytest.raises(ValueError, match="iterations must be positive"):
            momentum_optimizer.optimize_random(
                historical_data=pd.DataFrame(),
                parameter_ranges={"rsi_period": [10]},
                iterations=-1,
                initial_capital=1_000_000,
            )


# ======================================================================
# evaluate_parameters
# ======================================================================


class TestEvaluateParameters:
    """ParameterOptimizer.evaluate_parameters."""

    def test_evaluate_returns_metrics_dict(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """evaluate_parameters returns a metrics dictionary."""
        metrics = momentum_optimizer.evaluate_parameters(
            parameters={"rsi_period": 14, "stop_loss_pct": 0.05},
            historical_data=sample_dataframe,
            initial_capital=1_000_000,
        )
        assert isinstance(metrics, dict)
        assert "net_profit" in metrics
        assert "score" in metrics

    def test_evaluate_restores_params(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """evaluate_parameters restores original params after call."""
        original = momentum_optimizer._strategy.rsi_period
        momentum_optimizer.evaluate_parameters(
            parameters={"rsi_period": 99},
            historical_data=sample_dataframe,
            initial_capital=1_000_000,
        )
        assert momentum_optimizer._strategy.rsi_period == original


# ======================================================================
# rank_results
# ======================================================================


class TestRankResults:
    """ParameterOptimizer.rank_results."""

    def test_rank_results_by_metric(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """rank_results sorts by metric descending."""
        momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14, 20],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="score",
        )
        ranked = momentum_optimizer.rank_results(metric="score")
        assert len(ranked) > 0
        for i in range(len(ranked) - 1):
            assert ranked[i].score >= ranked[i + 1].score

    def test_rank_results_invalid_metric(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Invalid metric in rank_results raises ValueError."""
        with pytest.raises(ValueError, match="Unknown metric"):
            momentum_optimizer.rank_results(metric="bad_metric")


# ======================================================================
# Summary
# ======================================================================


class TestSummary:
    """ParameterOptimizer.summary."""

    def test_summary_before_run_returns_zeroed(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """summary before any run returns zeroed metrics."""
        s = momentum_optimizer.summary()
        assert s["total_evaluated"] == 0
        assert s["best_parameters"] == {}

    def test_summary_after_run_has_data(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """summary after a run contains evaluation results."""
        momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="score",
        )
        s = momentum_optimizer.summary()
        assert s["total_evaluated"] == 4
        assert len(s["top_10"]) > 0
        assert isinstance(s["average_score"], float)


# ======================================================================
# CSV export
# ======================================================================


class TestCsvExport:
    """ParameterOptimizer.export_csv."""

    def test_export_csv_creates_file(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """export_csv creates a CSV file."""
        momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14],
                "stop_loss_pct": [0.03],
            },
            initial_capital=1_000_000,
            metric="score",
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            csv_path = f.name

        try:
            momentum_optimizer.export_csv(csv_path)
            assert os.path.exists(csv_path)
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 2  # 2 combinations
        finally:
            os.unlink(csv_path)

    def test_export_csv_contains_headers(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """CSV has parameter and metric column headers."""
        momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10],
                "stop_loss_pct": [0.03],
            },
            initial_capital=1_000_000,
            metric="score",
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            csv_path = f.name

        try:
            momentum_optimizer.export_csv(csv_path)
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
            assert "rsi_period" in fieldnames
            assert "stop_loss_pct" in fieldnames
            assert "net_profit" in fieldnames
            assert "score" in fieldnames
        finally:
            os.unlink(csv_path)


# ======================================================================
# to_dict
# ======================================================================


class TestToDict:
    """ParameterOptimizer.to_dict."""

    def test_to_dict_returns_serializable(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """to_dict returns a dictionary with all results."""
        momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14],
                "stop_loss_pct": [0.03],
            },
            initial_capital=1_000_000,
            metric="score",
        )
        d = momentum_optimizer.to_dict()

        assert d["strategy_name"] == "MomentumStrategy"
        assert len(d["results"]) == 2
        assert isinstance(d["runtime_seconds"], float)


# ======================================================================
# Edge cases
# ======================================================================


class TestEdgeCases:
    """Edge cases and error resilience."""

    def test_small_dataframe(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """Small DataFrame with fewer rows than needed runs without error."""
        df = pd.DataFrame(
            {"Close": range(20), "Open": range(20),
             "High": range(20), "Low": range(20),
             "Volume": range(20)}
        )
        result = momentum_optimizer.optimize_grid(
            historical_data=df,
            parameter_grid={"rsi_period": [10]},
            initial_capital=1_000_000,
            metric="net_profit",
        )
        assert isinstance(result, OptimizationResult)
        # Small dataframe produces no trades → zeroed metrics
        assert result.best_metric == 0.0

    def test_multiple_grid_columns(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Grid with many columns still works."""
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid={
                "rsi_period": [10, 14],
                "rsi_buy_threshold": [50.0, 55.0],
                "rsi_sell_threshold": [35.0, 40.0],
                "stop_loss_pct": [0.03, 0.05],
            },
            initial_capital=1_000_000,
            metric="score",
        )
        # 2 × 2 × 2 × 2 = 16 combinations
        assert result.total_combinations == 16
        assert result.tested_combinations == 16

    def test_custom_grid_retains_order(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """Parameters retain their original key order in results."""
        grid = {
            "stop_loss_pct": [0.03, 0.05],
            "rsi_period": [14],
        }
        result = momentum_optimizer.optimize_grid(
            historical_data=sample_dataframe,
            parameter_grid=grid,
            initial_capital=1_000_000,
            metric="score",
        )
        for r in result.results:
            params = r.parameters
            assert "stop_loss_pct" in params
            assert "rsi_period" in params

    def test_all_metrics_accepted(
        self, momentum_optimizer: ParameterOptimizer, sample_dataframe: pd.DataFrame
    ) -> None:
        """All 11 valid metrics are accepted without error."""
        metrics = [
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
        ]
        for metric in metrics:
            result = momentum_optimizer.optimize_grid(
                historical_data=sample_dataframe,
                parameter_grid={"rsi_period": [14]},
                initial_capital=1_000_000,
                metric=metric,
            )
            assert isinstance(result, OptimizationResult)

    def test_empty_results_export_csv_does_not_crash(
        self, momentum_optimizer: ParameterOptimizer
    ) -> None:
        """export_csv with no results creates an empty CSV."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            csv_path = f.name

        try:
            # Should not raise despite no results
            momentum_optimizer.export_csv(csv_path)
            assert os.path.exists(csv_path)
        finally:
            os.unlink(csv_path)
