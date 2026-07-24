"""Tests for pure backtest performance metrics."""

from math import sqrt

import pytest

from src.backtest.metrics import (
    calculate_average_loss,
    calculate_average_win,
    calculate_cagr,
    calculate_expectancy,
    calculate_max_drawdown,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_total_return,
    calculate_trade_statistics,
    calculate_win_rate,
)


@pytest.fixture
def trades() -> list[dict[str, float]]:
    """Provide winning, losing, and breakeven trades."""
    return [{"return_pct": 10.0}, {"return_pct": -5.0}, {"return_pct": 0.0}]


def test_trade_metrics_calculate_expected_values(trades: list[dict[str, float]]) -> None:
    """Trade-return metrics calculate expected values."""
    assert calculate_win_rate(trades) == pytest.approx(100 / 3)
    assert calculate_profit_factor(trades) == 2.0
    assert calculate_average_win(trades) == 10.0
    assert calculate_average_loss(trades) == -5.0
    assert calculate_expectancy(trades) == pytest.approx(5 / 3)


def test_metrics_handle_empty_or_zero_denominator_input() -> None:
    """Metrics use safe defaults for empty and zero-denominator data."""
    assert calculate_win_rate([]) == 0.0
    assert calculate_profit_factor([{"return_pct": 5.0}]) == 0.0
    assert calculate_average_win([]) == 0.0
    assert calculate_average_loss([]) == 0.0
    assert calculate_expectancy([]) == 0.0
    assert calculate_total_return(0, 100) == 0.0
    assert calculate_cagr(100, 200, 0) == 0.0
    assert calculate_sharpe_ratio([1.0]) == 0.0


def test_portfolio_metrics_calculate_expected_values() -> None:
    """Equity and capital metrics calculate percentage values."""
    assert calculate_max_drawdown([100, 120, 90, 110]) == 25.0
    assert calculate_sharpe_ratio([1.0, 2.0, 3.0]) == pytest.approx(sqrt(6))
    assert calculate_total_return(100, 125) == 25.0
    assert calculate_cagr(100, 121, 2) == pytest.approx(10.0)


def test_trade_statistics_compiles_metrics(trades: list[dict[str, float]]) -> None:
    """The aggregate statistics function returns counts and metrics."""
    statistics = calculate_trade_statistics(trades)
    assert statistics["total_trades"] == 3
    assert statistics["winning_trades"] == 1
    assert statistics["losing_trades"] == 1
    assert statistics["breakeven_trades"] == 1
    assert statistics["profit_factor"] == 2.0
