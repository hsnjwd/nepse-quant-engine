"""Tests for the optimization subsystem (src/optimization)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.optimization.genetic import GeneticOptimizer, Individual
from src.optimization.kelly import KellyCriterion
from src.optimization.mpt import EfficientFrontier, MarkowitzOptimizer, PortfolioResult
from src.optimization.risk_parity import RiskParityOptimizer


def returns_frame(n: int = 150, seed: int = 5) -> pd.DataFrame:
    """Build a deterministic multi-asset returns frame."""
    rng = np.random.default_rng(seed)
    assets = ["A", "B", "C"]
    data = {a: list(rng.normal(0.0005, 0.02, n)) for a in assets}
    return pd.DataFrame(data)


class TestMarkowitzOptimizer:
    def test_requires_two_assets(self) -> None:
        with pytest.raises(ValueError):
            MarkowitzOptimizer(pd.DataFrame({"A": [1.0, 2.0]}))

    def test_min_variance(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        result = optimizer.minimum_variance_portfolio()
        assert isinstance(result, PortfolioResult)
        assert abs(np.sum(result.weights) - 1.0) < 1e-6
        assert result.volatility >= 0

    def test_max_sharpe(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        result = optimizer.max_sharpe_portfolio()
        assert abs(np.sum(result.weights) - 1.0) < 1e-6

    def test_efficient_frontier(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        frontier = optimizer.efficient_frontier(n_points=10)
        assert isinstance(frontier, EfficientFrontier)
        assert len(frontier.returns) > 0
        assert len(frontier.returns) == len(frontier.volatilities)

    def test_random_portfolios(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        portfolios = optimizer.random_portfolios(n_portfolios=50, seed=1)
        assert len(portfolios) == 50

    def test_optimize_objective(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        result = optimizer.optimize("equally_weighted")
        assert np.allclose(result.weights, 1 / 3, atol=1e-6)

    def test_optimize_unknown(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        with pytest.raises(ValueError):
            optimizer.optimize("bogus")

    def test_to_dict(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        data = optimizer.minimum_variance_portfolio().to_dict()
        assert "weights" in data
        assert "sharpe" in data

    def test_assets_property(self) -> None:
        optimizer = MarkowitzOptimizer(returns_frame())
        assert optimizer.assets == ["A", "B", "C"]


class TestRiskParityOptimizer:
    def test_optimize(self) -> None:
        optimizer = RiskParityOptimizer(returns_frame())
        result = optimizer.optimize()
        assert "weights" in result
        weights = list(result["weights"].values())
        assert abs(sum(weights) - 1.0) < 1e-4  # weights rounded to 6dp

    def test_risk_contributions(self) -> None:
        optimizer = RiskParityOptimizer(returns_frame())
        result = optimizer.optimize()
        contributions = list(result["risk_contributions"].values())
        assert len(contributions) == 3
        assert all(c > 0 for c in contributions)

    def test_assets(self) -> None:
        optimizer = RiskParityOptimizer(returns_frame())
        assert optimizer.assets == ["A", "B", "C"]

    def test_empty(self) -> None:
        optimizer = RiskParityOptimizer(pd.DataFrame())
        result = optimizer.optimize()
        assert result["weights"] == {}


class TestKellyCriterion:
    def test_positive_edge(self) -> None:
        result = KellyCriterion().calculate(0.6, 100.0, 50.0)
        assert result.full_kelly > 0
        assert result.half_kelly == result.full_kelly / 2
        assert result.quarter_kelly == result.full_kelly / 4

    def test_no_edge(self) -> None:
        result = KellyCriterion().calculate(0.5, 100.0, 100.0)
        assert result.full_kelly == 0.0
        assert any("no edge" in note for note in result.notes)

    def test_clamped(self) -> None:
        result = KellyCriterion().calculate(0.9, 1000.0, 1.0)
        assert result.full_kelly <= 1.0

    def test_invalid_win_rate(self) -> None:
        with pytest.raises(ValueError):
            KellyCriterion().calculate(1.5, 100.0, 50.0)

    def test_invalid_loss(self) -> None:
        with pytest.raises(ValueError):
            KellyCriterion().calculate(0.5, 100.0, 0.0)

    def test_from_trades(self) -> None:
        trades = [
            {"profit": 100.0},
            {"profit": 100.0},
            {"profit": -50.0},
            {"profit": -50.0},
        ]
        result = KellyCriterion.from_trades(trades)
        assert result.full_kelly >= 0

    def test_from_trades_empty(self) -> None:
        result = KellyCriterion.from_trades([])
        assert result.full_kelly == 0.0
        assert result.notes

    def test_to_dict(self) -> None:
        data = KellyCriterion().calculate(0.55, 100.0, 80.0).to_dict()
        assert "full_kelly" in data


class TestGeneticOptimizer:
    def test_run_finds_best(self) -> None:
        optimizer = GeneticOptimizer(
            param_space={"x": range(0, 10), "y": range(0, 10)},
            fitness_fn=lambda p: p["x"] + p["y"],
            population_size=10,
            generations=5,
            seed=1,
        )
        best, history = optimizer.run()
        assert isinstance(best, Individual)
        assert best.fitness >= 12  # far above random expectation (mean 9, max 18)
        assert len(history) == 5

    def test_validation(self) -> None:
        with pytest.raises(ValueError):
            GeneticOptimizer(
                param_space={"x": range(3)},
                fitness_fn=lambda p: 1.0,
                population_size=2,
            )

    def test_empty_space(self) -> None:
        optimizer = GeneticOptimizer(
            param_space={}, fitness_fn=lambda p: 1.0
        )
        best, history = optimizer.run()
        assert best is None
        assert history == []

    def test_progress_callback(self) -> None:
        calls: list[int] = []
        optimizer = GeneticOptimizer(
            param_space={"x": range(5)},
            fitness_fn=lambda p: float(p["x"]),
            population_size=8,
            generations=3,
            seed=2,
        )

        def _cb(gen: int, total: int, best: float) -> None:
            calls.append(gen)

        optimizer.run(progress_callback=_cb)
        assert calls == [1, 2, 3]

    def test_mutation_operator(self) -> None:
        optimizer = GeneticOptimizer(
            param_space={"x": range(100)},
            fitness_fn=lambda p: 1.0,
            population_size=8,
            generations=2,
            mutation_rate=0.5,
            seed=3,
        )
        original = {"x": 5}
        mutated = optimizer._mutate(original)
        assert "x" in mutated
        assert 0 <= mutated["x"] <= 99

    def test_tournament(self) -> None:
        optimizer = GeneticOptimizer(
            param_space={"x": range(10)},
            fitness_fn=lambda p: float(p["x"]),
            population_size=8,
            generations=2,
            seed=1,
        )
        population = [
            Individual(genes={"x": 1}, fitness=1.0),
            Individual(genes={"x": 9}, fitness=9.0),
            Individual(genes={"x": 4}, fitness=4.0),
        ]
        winner = optimizer._tournament(population, k=3)
        assert winner.fitness == 9.0

    def test_crossover(self) -> None:
        optimizer = GeneticOptimizer(
            param_space={"a": range(10), "b": range(10)},
            fitness_fn=lambda p: 1.0,
            population_size=8,
            generations=2,
            seed=1,
        )
        child = optimizer._crossover({"a": 1, "b": 2}, {"a": 9, "b": 8})
        assert set(child) == {"a", "b"}

    def test_to_dict(self) -> None:
        individual = Individual(genes={"x": 3}, fitness=3.0)
        data = individual.to_dict()
        assert data["genes"] == {"x": 3}
