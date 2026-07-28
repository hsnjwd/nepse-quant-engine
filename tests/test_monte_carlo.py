"""Comprehensive tests for the Monte Carlo Simulation Engine."""

from __future__ import annotations

import csv
import math
import tempfile
from typing import Any

import pytest

from src.simulation.monte_carlo import (
    MonteCarloSimulator,
    MonteCarloSummary,
    SimulationResult,
)


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def sample_trades() -> list[dict[str, Any]]:
    """Typical trade history with a mix of wins and losses."""
    return [
        {"net_profit": 1000.0, "return_pct": 2.0, "holding_days": 5},
        {"net_profit": -400.0, "return_pct": -0.8, "holding_days": 3},
        {"net_profit": 1500.0, "return_pct": 3.0, "holding_days": 7},
        {"net_profit": 200.0, "return_pct": 0.4, "holding_days": 2},
        {"net_profit": -100.0, "return_pct": -0.2, "holding_days": 4},
        {"net_profit": 3000.0, "return_pct": 6.0, "holding_days": 10},
        {"net_profit": -800.0, "return_pct": -1.6, "holding_days": 6},
        {"net_profit": 500.0, "return_pct": 1.0, "holding_days": 3},
        {"net_profit": -50.0, "return_pct": -0.1, "holding_days": 1},
        {"net_profit": 1200.0, "return_pct": 2.4, "holding_days": 8},
    ]


@pytest.fixture
def all_win_trades() -> list[dict[str, Any]]:
    """Trades that are all profitable."""
    return [
        {"net_profit": 500.0, "return_pct": 1.0, "holding_days": 3},
        {"net_profit": 300.0, "return_pct": 0.6, "holding_days": 5},
        {"net_profit": 800.0, "return_pct": 1.6, "holding_days": 4},
    ]


@pytest.fixture
def all_loss_trades() -> list[dict[str, Any]]:
    """Trades that are all losing."""
    return [
        {"net_profit": -500.0, "return_pct": -1.0, "holding_days": 3},
        {"net_profit": -300.0, "return_pct": -0.6, "holding_days": 5},
        {"net_profit": -800.0, "return_pct": -1.6, "holding_days": 4},
    ]


@pytest.fixture
def single_trade() -> list[dict[str, Any]]:
    """Single trade edge case."""
    return [{"net_profit": 1000.0, "return_pct": 2.0, "holding_days": 5}]


@pytest.fixture
def simulator() -> MonteCarloSimulator:
    """Default simulator with small simulations for fast tests."""
    return MonteCarloSimulator(simulations=100, random_seed=42)


# ======================================================================
# Test SimulationResult dataclass
# ======================================================================


class TestSimulationResult:
    """Verify the SimulationResult dataclass and its to_dict()."""

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all expected fields."""
        result = SimulationResult(
            simulation_number=1,
            ending_equity=5500.0,
            net_profit=5500.0,
            max_drawdown=8.5,
            return_pct=5.5,
            win_rate=70.0,
            profit_factor=2.5,
        )
        d = result.to_dict()

        assert d["simulation_number"] == 1
        assert d["ending_equity"] == 5500.0
        assert d["net_profit"] == 5500.0
        assert d["max_drawdown"] == 8.5
        assert d["return_pct"] == 5.5
        assert d["win_rate"] == 70.0
        assert d["profit_factor"] == 2.5
        assert len(d) == 7

    def test_to_dict_rounds_values(self) -> None:
        """to_dict() rounds float values to 2 decimal places."""
        result = SimulationResult(
            simulation_number=1,
            ending_equity=5500.555,
            net_profit=5500.555,
            max_drawdown=8.555,
            return_pct=5.555,
            win_rate=70.555,
            profit_factor=2.5555,
        )
        d = result.to_dict()
        assert d["ending_equity"] == 5500.56
        assert d["net_profit"] == 5500.56
        # Note: 8.555 in IEEE-754 float is actually 8.55499..., so
        # round() yields 8.55 (banker's rounding rounds .555 to .55).
        assert d["max_drawdown"] == 8.55
        assert d["return_pct"] == 5.55
        assert d["win_rate"] == 70.56
        assert d["profit_factor"] == 2.5555  # 4 decimal places

    def test_negative_values(self) -> None:
        """Negative metrics are handled correctly."""
        result = SimulationResult(
            simulation_number=1,
            ending_equity=-1000.0,
            net_profit=-1000.0,
            max_drawdown=15.0,
            return_pct=-10.0,
            win_rate=0.0,
            profit_factor=0.0,
        )
        d = result.to_dict()
        assert d["ending_equity"] == -1000.0
        assert d["win_rate"] == 0.0


# ======================================================================
# Test MonteCarloSummary dataclass
# ======================================================================


class TestMonteCarloSummary:
    """Verify the MonteCarloSummary dataclass and its to_dict()."""

    def test_to_dict_contains_all_keys(self) -> None:
        """to_dict() returns a flat dictionary with expected keys."""
        summary = MonteCarloSummary(
            simulations=100,
            mean_return=1500.0,
            median_return=1200.0,
            best_return=5000.0,
            worst_return=-1000.0,
            mean_drawdown=8.0,
            max_drawdown=25.0,
            probability_of_profit=80.0,
            probability_of_loss=18.0,
            probability_of_ruin=2.0,
            value_at_risk_95=-500.0,
            conditional_var_95=-800.0,
            confidence_interval={"lower": -300.0, "upper": 3500.0},
            percentiles={
                "p5": -400.0,
                "p10": -200.0,
                "p25": 300.0,
                "p50": 1200.0,
                "p75": 2500.0,
                "p90": 3800.0,
                "p95": 4500.0,
                "p99": 4900.0,
            },
        )
        d = summary.to_dict()

        assert d["simulations"] == 100
        assert d["mean_return"] == 1500.0
        assert d["median_return"] == 1200.0
        assert d["best_return"] == 5000.0
        assert d["worst_return"] == -1000.0
        assert d["probability_of_profit"] == 80.0
        assert d["value_at_risk_95"] == -500.0
        assert d["conditional_var_95"] == -800.0
        assert d["confidence_interval"]["lower"] == -300.0
        assert d["percentiles"]["p5"] == -400.0
        assert d["percentiles"]["p99"] == 4900.0

        # Verify all expected top-level keys
        expected_keys = {
            "simulations",
            "mean_return",
            "median_return",
            "best_return",
            "worst_return",
            "mean_drawdown",
            "max_drawdown",
            "probability_of_profit",
            "probability_of_loss",
            "probability_of_ruin",
            "value_at_risk_95",
            "conditional_var_95",
            "confidence_interval",
            "percentiles",
        }
        assert set(d.keys()) == expected_keys


# ======================================================================
# Test constructor validation
# ======================================================================


class TestConstructor:
    """Verify constructor parameter validation."""

    def test_default_parameters(self) -> None:
        """Default constructor creates a valid simulator."""
        sim = MonteCarloSimulator()
        assert sim._simulations == 10_000
        assert sim._confidence_level == 0.95
        assert sim._rng is not None

    def test_custom_valid_parameters(self) -> None:
        """Custom valid parameters are accepted."""
        sim = MonteCarloSimulator(simulations=500, confidence_level=0.99, random_seed=123)
        assert sim._simulations == 500
        assert sim._confidence_level == 0.99

    def test_invalid_simulations_zero(self) -> None:
        """Zero simulations raises ValueError."""
        with pytest.raises(ValueError, match="simulations must be positive"):
            MonteCarloSimulator(simulations=0)

    def test_invalid_simulations_negative(self) -> None:
        """Negative simulations raises ValueError."""
        with pytest.raises(ValueError, match="simulations must be positive"):
            MonteCarloSimulator(simulations=-10)

    def test_invalid_confidence_level_zero(self) -> None:
        """Zero confidence level raises ValueError."""
        with pytest.raises(ValueError, match="confidence_level must be in"):
            MonteCarloSimulator(confidence_level=0.0)

    def test_invalid_confidence_level_above_one(self) -> None:
        """Confidence level above 1 raises ValueError."""
        with pytest.raises(ValueError, match="confidence_level must be in"):
            MonteCarloSimulator(confidence_level=1.5)

    def test_invalid_confidence_level_negative(self) -> None:
        """Negative confidence level raises ValueError."""
        with pytest.raises(ValueError, match="confidence_level must be in"):
            MonteCarloSimulator(confidence_level=-0.1)


# ======================================================================
# Test bootstrap simulation
# ======================================================================


class TestBootstrapSimulation:
    """Verify the bootstrap resampling method."""

    def test_returns_list_of_results(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Bootstrap returns a list of SimulationResult."""
        results = simulator.simulate_bootstrap(sample_trades)
        assert isinstance(results, list)
        assert len(results) == 100
        assert all(isinstance(r, SimulationResult) for r in results)

    def test_results_have_unique_numbers(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Each result has a unique simulation_number."""
        results = simulator.simulate_bootstrap(sample_trades)
        nums = [r.simulation_number for r in results]
        assert nums == list(range(1, 101))

    def test_empty_trades_raises(self, simulator: MonteCarloSimulator) -> None:
        """Empty trade list raises ValueError."""
        with pytest.raises(ValueError, match="Trade list is empty"):
            simulator.simulate_bootstrap([])

    def test_single_trade(self, simulator: MonteCarloSimulator, single_trade: list[dict[str, Any]]) -> None:
        """Bootstrap works with a single trade (always samples that trade)."""
        results = simulator.simulate_bootstrap(single_trade)
        assert len(results) == 100
        assert all(r.net_profit == 1000.0 for r in results)
        assert all(r.win_rate == 100.0 for r in results)

    def test_reproducible_seed(self) -> None:
        """Same seed produces identical results."""
        sim_a = MonteCarloSimulator(simulations=50, random_seed=42)
        sim_b = MonteCarloSimulator(simulations=50, random_seed=42)
        trades = [{"net_profit": 100.0, "return_pct": 1.0, "holding_days": 2},
                   {"net_profit": -50.0, "return_pct": -0.5, "holding_days": 3}]
        results_a = sim_a.simulate_bootstrap(trades)
        results_b = sim_b.simulate_bootstrap(trades)
        for ra, rb in zip(results_a, results_b):
            assert ra.ending_equity == rb.ending_equity

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different results."""
        sim_a = MonteCarloSimulator(simulations=50, random_seed=42)
        sim_b = MonteCarloSimulator(simulations=50, random_seed=99)
        trades = [{"net_profit": 100.0, "return_pct": 1.0, "holding_days": 2},
                   {"net_profit": -50.0, "return_pct": -0.5, "holding_days": 3}]
        results_a = sim_a.simulate_bootstrap(trades)
        results_b = sim_b.simulate_bootstrap(trades)
        # At least one result should differ (extremely unlikely to match)
        assert any(
            ra.ending_equity != rb.ending_equity
            for ra, rb in zip(results_a, results_b)
        )


# ======================================================================
# Test shuffle simulation
# ======================================================================


class TestShuffleSimulation:
    """Verify the shuffle method."""

    def test_returns_list_of_results(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Shuffle returns a list of SimulationResult."""
        results = simulator.simulate_shuffle(sample_trades)
        assert isinstance(results, list)
        assert len(results) == 100
        assert all(isinstance(r, SimulationResult) for r in results)

    def test_shuffle_sum_net_profit_constant(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Shuffle preserves the total net profit (order only changes)."""
        total_np = sum(t["net_profit"] for t in sample_trades)
        results = simulator.simulate_shuffle(sample_trades)
        for r in results:
            assert abs(r.net_profit - total_np) < 0.01

    def test_empty_trades_raises(self, simulator: MonteCarloSimulator) -> None:
        """Empty trade list raises ValueError."""
        with pytest.raises(ValueError, match="Trade list is empty"):
            simulator.simulate_shuffle([])

    def test_single_trade(self, simulator: MonteCarloSimulator, single_trade: list[dict[str, Any]]) -> None:
        """Shuffle with a single trade returns that trade's metrics."""
        results = simulator.simulate_shuffle(single_trade)
        assert all(r.net_profit == 1000.0 for r in results)
        assert all(r.win_rate == 100.0 for r in results)

    def test_reproducible_seed(self) -> None:
        """Same seed produces identical shuffle results."""
        sim_a = MonteCarloSimulator(simulations=50, random_seed=42)
        sim_b = MonteCarloSimulator(simulations=50, random_seed=42)
        trades = [{"net_profit": 100.0, "return_pct": 1.0, "holding_days": 2},
                   {"net_profit": -50.0, "return_pct": -0.5, "holding_days": 3}]
        results_a = sim_a.simulate_shuffle(trades)
        results_b = sim_b.simulate_shuffle(trades)
        for ra, rb in zip(results_a, results_b):
            assert ra.ending_equity == rb.ending_equity


# ======================================================================
# Test return compounding simulation
# ======================================================================


class TestReturnSimulation:
    """Verify the return-compounding method."""

    def test_returns_list_of_results(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Return simulation returns a list of SimulationResult."""
        results = simulator.simulate_returns(sample_trades)
        assert isinstance(results, list)
        assert len(results) == 100
        assert all(isinstance(r, SimulationResult) for r in results)

    def test_positive_compounding(self, simulator: MonteCarloSimulator, all_win_trades: list[dict[str, Any]]) -> None:
        """All-positive returns produce positive ending equity."""
        results = simulator.simulate_returns(all_win_trades)
        assert all(r.ending_equity > 100.0 for r in results)

    def test_empty_trades_raises(self, simulator: MonteCarloSimulator) -> None:
        """Empty trade list raises ValueError."""
        with pytest.raises(ValueError, match="Trade list is empty"):
            simulator.simulate_returns([])

    def test_single_trade(self, simulator: MonteCarloSimulator, single_trade: list[dict[str, Any]]) -> None:
        """Single trade return simulation."""
        results = simulator.simulate_returns(single_trade)
        # Starting from 100, 2% return = 102
        assert all(abs(r.ending_equity - 102.0) < 0.01 for r in results)


# ======================================================================
# Test summary
# ======================================================================


class TestSummary:
    """Verify the aggregate summary computation."""

    def test_empty_results_raises(self, simulator: MonteCarloSimulator) -> None:
        """Empty results list raises ValueError."""
        with pytest.raises(ValueError, match="Cannot compute summary"):
            simulator.summary([])

    def test_summary_has_expected_fields(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Summary returns a MonteCarloSummary with populated fields."""
        results = simulator.simulate_bootstrap(sample_trades)
        summary = simulator.summary(results)

        assert isinstance(summary, MonteCarloSummary)
        assert summary.simulations == 100
        assert summary.mean_return != 0.0
        assert summary.median_return != 0.0
        assert summary.best_return >= summary.mean_return
        assert summary.worst_return <= summary.mean_return

    def test_probabilities_sum_to_100(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Probability of profit + loss may not sum to 100 if breakeven exists."""
        results = simulator.simulate_bootstrap(sample_trades)
        summary = simulator.summary(results)
        # profit + loss + (implicit) breakeven = 100
        assert summary.probability_of_profit + summary.probability_of_loss <= 100.0 + 1e-9

    def test_all_win_probability(self, simulator: MonteCarloSimulator, all_win_trades: list[dict[str, Any]]) -> None:
        """All-win trades produce high probability of profit."""
        results = simulator.simulate_bootstrap(all_win_trades)
        summary = simulator.summary(results)
        assert summary.probability_of_profit > 99.0

    def test_all_loss_probability(self, simulator: MonteCarloSimulator, all_loss_trades: list[dict[str, Any]]) -> None:
        """All-loss trades produce high probability of loss."""
        results = simulator.simulate_bootstrap(all_loss_trades)
        summary = simulator.summary(results)
        assert summary.probability_of_loss > 99.0

    def test_confidence_interval(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Confidence interval has lower and upper bounds."""
        results = simulator.simulate_bootstrap(sample_trades)
        summary = simulator.summary(results)
        assert "lower" in summary.confidence_interval
        assert "upper" in summary.confidence_interval
        assert summary.confidence_interval["lower"] <= summary.confidence_interval["upper"]

    def test_percentiles_are_ordered(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """Percentiles are monotonically increasing."""
        results = simulator.simulate_bootstrap(sample_trades)
        summary = simulator.summary(results)
        p = summary.percentiles
        assert p["p5"] <= p["p10"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p90"] <= p["p95"] <= p["p99"]

    def test_var_less_than_cvar(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """VaR (5th percentile) should be >= CVaR (mean of tail)."""
        results = simulator.simulate_bootstrap(sample_trades)
        summary = simulator.summary(results)
        assert summary.value_at_risk_95 >= summary.conditional_var_95


# ======================================================================
# Test CSV export
# ======================================================================


class TestCsvExport:
    """Verify CSV export functionality."""

    def test_export_creates_file(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """export_csv creates a CSV file."""
        results = simulator.simulate_bootstrap(sample_trades)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_path = f.name
        try:
            simulator.export_csv(csv_path, results)
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 100
        finally:
            import os
            os.unlink(csv_path)

    def test_export_contains_headers(self, simulator: MonteCarloSimulator, sample_trades: list[dict[str, Any]]) -> None:
        """CSV has correct column headers."""
        results = simulator.simulate_bootstrap(sample_trades)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_path = f.name
        try:
            simulator.export_csv(csv_path, results)
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
            expected = [
                "simulation_number",
                "ending_equity",
                "net_profit",
                "max_drawdown",
                "return_pct",
                "win_rate",
                "profit_factor",
            ]
            assert headers == expected
        finally:
            import os
            os.unlink(csv_path)

    def test_export_empty_raises(self, simulator: MonteCarloSimulator) -> None:
        """Exporting empty results raises ValueError."""
        with pytest.raises(ValueError, match="Cannot export empty"):
            simulator.export_csv("/tmp/nonexistent.csv", [])


# ======================================================================
# Test equity curve helpers
# ======================================================================


class TestEquityHelpers:
    """Verify internal equity curve computation helpers."""

    def test_build_equity_curve(self) -> None:
        """_build_equity_curve produces correct cumulative values."""
        curve = MonteCarloSimulator._build_equity_curve([100.0, -50.0, 200.0])
        assert curve == [0.0, 100.0, 50.0, 250.0]

    def test_build_equity_curve_empty(self) -> None:
        """Empty net profits returns [0.0]."""
        curve = MonteCarloSimulator._build_equity_curve([])
        assert curve == [0.0]

    def test_build_return_equity(self) -> None:
        """_build_return_equity compounds correctly."""
        curve = MonteCarloSimulator._build_return_equity([10.0, -5.0])
        # 100 * 1.10 = 110, 110 * 0.95 = 104.5
        assert curve == pytest.approx([100.0, 110.0, 104.5], rel=1e-9)

    def test_build_return_equity_empty(self) -> None:
        """Empty returns returns [100.0]."""
        curve = MonteCarloSimulator._build_return_equity([])
        assert curve == [100.0]

    def test_compute_equity_drawdown(self) -> None:
        """_compute_equity_drawdown computes correctly."""
        curve = [0.0, 100.0, 50.0, 200.0, 150.0]
        dd = MonteCarloSimulator._compute_equity_drawdown(curve)
        # Peak at 200, trough at 150: (200-150)/200 * 100 = 25%
        # Also: peak at 100, trough at 50: (100-50)/100*100 = 50%
        # Max is 50%
        assert dd == 50.0

    def test_compute_equity_drawdown_monotonic_rise(self) -> None:
        """Monotonically rising curve has 0 drawdown."""
        dd = MonteCarloSimulator._compute_equity_drawdown([0.0, 50.0, 100.0, 150.0])
        assert dd == 0.0

    def test_compute_equity_drawdown_single_point(self) -> None:
        """Single-point curve has 0 drawdown."""
        dd = MonteCarloSimulator._compute_equity_drawdown([100.0])
        assert dd == 0.0

    def test_equity_return_pct(self) -> None:
        """_equity_return_pct computes return from peak."""
        curve = [0.0, 100.0, 150.0, 120.0]
        ret = MonteCarloSimulator._equity_return_pct(curve)
        # Peak=150, final=120: (120-150)/150 * 100 = -20%
        assert ret == -20.0

    def test_equity_return_pct_never_positive(self) -> None:
        """Curve that never goes positive returns 0."""
        curve = [0.0, -10.0, -20.0]
        ret = MonteCarloSimulator._equity_return_pct(curve)
        assert ret == 0.0

    def test_percentile_single_value(self) -> None:
        """Percentile of a single-value list returns that value."""
        p = MonteCarloSimulator._percentile([42.0], 50)
        assert p == 42.0

    def test_percentile_empty(self) -> None:
        """Percentile of an empty list returns 0."""
        p = MonteCarloSimulator._percentile([], 50)
        assert p == 0.0

    def test_percentile_median_even(self) -> None:
        """Median of even-length list."""
        p = MonteCarloSimulator._percentile([10.0, 20.0, 30.0, 40.0], 50)
        # k = 50/100 * 3 = 1.5, f=1, c=2
        # d0 = 20 * (2-1.5) = 10, d1 = 30 * (1.5-1) = 15
        # result = 25
        assert p == 25.0

    def test_percentile_median_odd(self) -> None:
        """Median of odd-length list."""
        p = MonteCarloSimulator._percentile([10.0, 30.0, 50.0], 50)
        # k = 0.5 * 2 = 1.0, integer -> sorted_data[1] = 30
        assert p == 30.0

    def test_average_equity_curve(self) -> None:
        """_average_equity_curve computes pointwise mean."""
        curves = [
            [0.0, 100.0, 200.0],
            [0.0, 50.0, 100.0],
        ]
        avg = MonteCarloSimulator._average_equity_curve(curves)
        assert avg == [0.0, 75.0, 150.0]

    def test_average_equity_curve_empty(self) -> None:
        """Empty curves list returns empty list."""
        avg = MonteCarloSimulator._average_equity_curve([])
        assert avg == []


# ======================================================================
# Test edge cases
# ======================================================================


class TestEdgeCases:
    """Edge case and error handling tests."""

    def test_simulations_progress_logging(self, sample_trades: list[dict[str, Any]]) -> None:
        """Large simulation runs without error."""
        sim = MonteCarloSimulator(simulations=500, random_seed=42)
        results = sim.simulate_bootstrap(sample_trades)
        assert len(results) == 500

    def test_missing_keys(self, simulator: MonteCarloSimulator) -> None:
        """Trades with missing keys default to 0."""
        trades: list[dict[str, Any]] = [
            {"net_profit": 100.0},  # missing return_pct, holding_days
        ]
        results = simulator.simulate_bootstrap(trades)
        assert all(r.net_profit == 100.0 for r in results)

    def test_empty_net_profit(self, simulator: MonteCarloSimulator) -> None:
        """Zero net profit trades are treated as breakeven."""
        trades = [
            {"net_profit": 0.0, "return_pct": 0.0, "holding_days": 1},
            {"net_profit": 0.0, "return_pct": 0.0, "holding_days": 2},
        ]
        results = simulator.simulate_bootstrap(trades)
        assert all(r.net_profit == 0.0 for r in results)
        assert all(r.win_rate == 0.0 for r in results)

    def test_large_positive_returns(self, simulator: MonteCarloSimulator) -> None:
        """Very large positive returns compound correctly."""
        trades = [{"net_profit": 1_000_000.0, "return_pct": 100.0, "holding_days": 10}]
        results = simulator.simulate_returns(trades)
        # 100 + 100 * 100/100 = 200
        assert all(abs(r.ending_equity - 200.0) < 0.01 for r in results)

    def test_ruin_probability_high_for_all_loss(self, simulator: MonteCarloSimulator, all_loss_trades: list[dict[str, Any]]) -> None:
        """All-loss trades should have non-zero probability of ruin."""
        results = simulator.simulate_bootstrap(all_loss_trades)
        summary = simulator.summary(results)
        assert summary.probability_of_ruin > 0
