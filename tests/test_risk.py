"""Tests for the Risk Lab subsystem (src/risk/lab)."""

from __future__ import annotations

import numpy as np
import pytest

from src.risk.lab import (
    MonteCarloLabResult,
    RiskLab,
    StressTestResult,
    VaRResult,
)


def make_returns(n: int = 250, seed: int = 7) -> np.ndarray:
    """Build a deterministic returns series for tests."""
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.015, n)


class TestVaR:
    def test_historical(self) -> None:
        lab = RiskLab(make_returns())
        result = lab.var(confidence=0.95, method="historical")
        assert isinstance(result, VaRResult)
        assert result.method == "historical"
        assert result.var >= 0
        assert result.cvar >= 0

    def test_parametric(self) -> None:
        lab = RiskLab(make_returns())
        result = lab.var(confidence=0.95, method="parametric")
        assert result.method == "parametric"
        assert result.std > 0
        assert result.var >= 0

    def test_monte_carlo_var(self) -> None:
        lab = RiskLab(make_returns())
        result = lab.var(confidence=0.95, method="monte_carlo")
        assert result.method == "monte_carlo"
        assert result.var >= 0

    def test_invalid_confidence(self) -> None:
        lab = RiskLab(make_returns())
        with pytest.raises(ValueError):
            lab.var(confidence=1.0)

    def test_to_dict(self) -> None:
        lab = RiskLab(make_returns())
        data = lab.var().to_dict()
        assert "var" in data
        assert "cvar" in data
        assert "method" in data


class TestStressTest:
    def test_scenarios(self) -> None:
        lab = RiskLab(make_returns(), portfolio_value=1_000_000)
        results = lab.stress_test()
        assert len(results) == 5
        assert all(isinstance(r, StressTestResult) for r in results)

    def test_market_crash(self) -> None:
        lab = RiskLab(make_returns(), portfolio_value=1_000_000)
        crash = [r for r in lab.stress_test() if r.scenario == "market_crash"][0]
        assert crash.end_value == pytest.approx(800_000)

    def test_custom_stress(self) -> None:
        lab = RiskLab(make_returns(), portfolio_value=500_000)
        result = lab.custom_stress(-0.10, scenario="flash")
        assert result.scenario == "flash"
        assert result.end_value == pytest.approx(450_000)

    def test_to_dict(self) -> None:
        lab = RiskLab(make_returns())
        data = lab.custom_stress(-0.05).to_dict()
        assert "scenario" in data
        assert "loss_pct" in data


class TestMonteCarlo:
    def test_monte_carlo_result(self) -> None:
        lab = RiskLab(make_returns())
        result = lab.monte_carlo(simulations=1000, horizon=50)
        assert isinstance(result, MonteCarloLabResult)
        assert result.simulations == 1000
        assert result.ending_values.shape == (1000,)
        assert result.mean_ending > 0
        assert 0 <= result.probability_of_loss <= 1
        assert 0 <= result.probability_of_ruin <= 1

    def test_invalid_simulations(self) -> None:
        lab = RiskLab(make_returns())
        with pytest.raises(ValueError):
            lab.monte_carlo(simulations=0)

    def test_drawdown_probability(self) -> None:
        lab = RiskLab(make_returns())
        result = lab.drawdown_probability(
            threshold_pct=20, simulations=500, horizon=50
        )
        assert "probability" in result
        assert 0 <= result["probability"] <= 1

    def test_recovery_analysis(self) -> None:
        lab = RiskLab(make_returns())
        result = lab.recovery_analysis(
            simulations=500, horizon=252, target_return_pct=5
        )
        assert "recovery_probability" in result
        assert 0 <= result["recovery_probability"] <= 1

    def test_to_dict(self) -> None:
        lab = RiskLab(make_returns())
        data = lab.monte_carlo(simulations=100, horizon=20).to_dict()
        assert data["simulations"] == 100
        assert "var_95" in data
