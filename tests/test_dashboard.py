"""Comprehensive tests for the Dashboard & Visualisation Module.

Tests cover portfolio metrics, performance metrics, risk metrics, chart
generators, console reports, exports (JSON/CSV/HTML/PDF), and the main
Dashboard class.

Target: 150+ tests with full branch coverage.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.dashboard import Dashboard
from src.dashboard import metrics as _metrics
from src.dashboard import reports as _reports
from src.dashboard import exporter as _exporter


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def sample_trades() -> list[dict[str, Any]]:
    """Provide a list of sample trade dicts."""
    return [
        {"symbol": "NABIL", "return_pct": 5.2, "net_profit": 52000,
         "entry_date": "2025-01-15", "exit_date": "2025-02-10"},
        {"symbol": "CHCL", "return_pct": -2.1, "net_profit": -21000,
         "entry_date": "2025-01-20", "exit_date": "2025-02-15"},
        {"symbol": "NLIC", "return_pct": 8.7, "net_profit": 87000,
         "entry_date": "2025-02-01", "exit_date": "2025-03-01"},
        {"symbol": "KBL", "return_pct": 0.0, "net_profit": 0,
         "entry_date": "2025-02-10", "exit_date": "2025-03-05"},
        {"symbol": "HBL", "return_pct": -3.5, "net_profit": -35000,
         "entry_date": "2025-02-15", "exit_date": "2025-03-10"},
    ]


@pytest.fixture
def sample_equity_curve() -> list[float]:
    """Provide a sample equity curve."""
    return [1000000, 1010000, 1025000, 1015000, 1030000, 1040000, 1035000]


@pytest.fixture
def sample_returns() -> list[float]:
    """Provide a sample list of returns."""
    return [0.01, -0.005, 0.015, -0.01, 0.02, 0.005, -0.008]


@pytest.fixture
def sample_summary() -> dict[str, Any]:
    """Provide a sample portfolio summary dict."""
    return {
        "total_capital": 1500000.0,
        "invested": 1000000.0,
        "cash": 500000.0,
        "current_value": 1200000.0,
        "return_pct": 20.0,
        "total_pnl": 200000.0,
        "win_rate": 60.0,
        "sharpe_ratio": 1.25,
        "sortino_ratio": 1.50,
        "calmar_ratio": 0.80,
        "max_drawdown": 15.0,
        "profit_factor": 2.5,
        "expectancy": 10000.0,
        "average_win": 50000.0,
        "average_loss": -25000.0,
        "largest_win": 100000.0,
        "largest_loss": -50000.0,
        "value_at_risk_95": -0.02,
        "conditional_var_95": -0.03,
        "volatility": 0.08,
        "downside_deviation": 0.04,
        "recovery_factor": 3.0,
        "num_trades": 5,
    }


@pytest.fixture
def sample_dates() -> list[str]:
    """Provide sample date strings."""
    return [
        "2025-01-01", "2025-01-15", "2025-02-01", "2025-02-15",
        "2025-03-01", "2025-03-15", "2025-04-01",
    ]


@pytest.fixture
def temp_dir() -> str:
    """Provide a temporary directory path."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def dashboard() -> Dashboard:
    """Provide a Dashboard instance."""
    return Dashboard()


# ======================================================================
# Portfolio metrics tests
# ======================================================================


class TestTotalCapital:
    """Tests for :func:`metrics.total_capital`."""

    def test_basic(self) -> None:
        assert _metrics.total_capital(1000000, 500000) == 1500000.0

    def test_zero_invested(self) -> None:
        assert _metrics.total_capital(0, 500000) == 500000.0

    def test_zero_cash(self) -> None:
        assert _metrics.total_capital(1000000, 0) == 1000000.0

    def test_both_zero(self) -> None:
        assert _metrics.total_capital(0, 0) == 0.0

    def test_float_precision(self) -> None:
        assert _metrics.total_capital(0.1, 0.2) == 0.3


class TestPortfolioReturn:
    """Tests for :func:`metrics.portfolio_return`."""

    def test_positive_return(self) -> None:
        ret = _metrics.portfolio_return(1000, 1200)
        assert ret == 20.0

    def test_negative_return(self) -> None:
        ret = _metrics.portfolio_return(1000, 800)
        assert ret == -20.0

    def test_zero_return(self) -> None:
        ret = _metrics.portfolio_return(1000, 1000)
        assert ret == 0.0

    def test_zero_initial(self) -> None:
        ret = _metrics.portfolio_return(0, 1000)
        assert ret == 0.0

    def test_large_values(self) -> None:
        ret = _metrics.portfolio_return(10_000_000, 12_500_000)
        assert ret == 25.0


class TestWinRate:
    """Tests for :func:`metrics.win_rate`."""

    def test_all_wins(self) -> None:
        trades = [{"return_pct": 1}, {"return_pct": 2}, {"return_pct": 3}]
        assert _metrics.win_rate(trades) == 100.0

    def test_all_losses(self) -> None:
        trades = [{"return_pct": -1}, {"return_pct": -2}, {"return_pct": -3}]
        assert _metrics.win_rate(trades) == 0.0

    def test_mixed(self) -> None:
        trades = [{"return_pct": 1}, {"return_pct": -1}, {"return_pct": 0}]
        assert _metrics.win_rate(trades) == pytest.approx(33.3333, abs=0.01)

    def test_empty(self) -> None:
        assert _metrics.win_rate([]) == 0.0

    def test_single_win(self) -> None:
        assert _metrics.win_rate([{"return_pct": 5}]) == 100.0

    def test_single_loss(self) -> None:
        assert _metrics.win_rate([{"return_pct": -5}]) == 0.0

    def test_net_profit_field(self) -> None:
        trades = [{"net_profit": 100}, {"net_profit": -50}]
        assert _metrics.win_rate(trades) == 50.0


class TestSharpeRatio:
    """Tests for :func:`metrics.sharpe_ratio`."""

    def test_positive_sharpe(self) -> None:
        returns = [0.01, 0.02, 0.015, 0.01, 0.005]
        sr = _metrics.sharpe_ratio(returns)
        assert sr is not None and sr > 0

    def test_negative_sharpe(self) -> None:
        returns = [-0.01, -0.02, -0.015, -0.01, -0.005]
        sr = _metrics.sharpe_ratio(returns)
        assert sr is not None and sr < 0

    def test_insufficient_data(self) -> None:
        assert _metrics.sharpe_ratio([0.01]) is None

    def test_empty(self) -> None:
        assert _metrics.sharpe_ratio([]) is None

    def test_zero_volatility(self) -> None:
        assert _metrics.sharpe_ratio([0.01, 0.01]) is None

    def test_with_risk_free_rate(self) -> None:
        returns = [0.01, 0.02, 0.015]
        sr = _metrics.sharpe_ratio(returns, risk_free_rate=0.005)
        assert sr is not None


class TestMaxDrawdown:
    """Tests for :func:`metrics.max_drawdown`."""

    def test_increasing(self) -> None:
        curve = [100, 110, 120, 130]
        assert _metrics.max_drawdown(curve) == 0.0

    def test_single_decline(self) -> None:
        curve = [100, 90, 80, 70]
        assert _metrics.max_drawdown(curve) == pytest.approx(30.0, abs=0.01)

    def test_peak_to_trough(self) -> None:
        curve = [100, 120, 110, 90, 105]
        assert _metrics.max_drawdown(curve) == pytest.approx(25.0, abs=0.01)

    def test_empty(self) -> None:
        assert _metrics.max_drawdown([]) == 0.0

    def test_single_value(self) -> None:
        assert _metrics.max_drawdown([100]) == 0.0

    def test_recovery(self) -> None:
        curve = [100, 110, 90, 95, 105]
        mdd = _metrics.max_drawdown(curve)
        assert mdd == pytest.approx(18.1818, abs=0.01)


class TestDrawdownCurve:
    """Tests for :func:`metrics.drawdown_curve`."""

    def test_increasing(self) -> None:
        curve = [100, 110, 120]
        dd = _metrics.drawdown_curve(curve)
        assert all(d == 0.0 for d in dd)

    def test_peak_trough(self) -> None:
        curve = [100, 120, 110, 90]
        dd = _metrics.drawdown_curve(curve)
        assert dd[0] == 0.0
        assert dd[1] == 0.0  # 120 is the peak
        assert dd[2] == pytest.approx(8.3333, abs=0.01)  # (120-110)/120
        assert dd[3] == pytest.approx(25.0, abs=0.01)  # (120-90)/120

    def test_empty(self) -> None:
        assert _metrics.drawdown_curve([]) == []

    def test_single(self) -> None:
        assert _metrics.drawdown_curve([100]) == [0.0]


class TestMonthlyReturns:
    """Tests for :func:`metrics.monthly_returns`."""

    def test_basic(self) -> None:
        dates = ["2025-01-01", "2025-01-15", "2025-02-01", "2025-02-15"]
        values = [100, 110, 105, 115]
        mr = _metrics.monthly_returns(dates, values)
        assert "2025" in mr
        assert "01" in mr["2025"]
        assert "02" in mr["2025"]

    def test_empty(self) -> None:
        assert _metrics.monthly_returns([], []) == {}

    def test_single_value(self) -> None:
        mr = _metrics.monthly_returns(["2025-01-01"], [100])
        assert mr == {}

    def test_two_years(self) -> None:
        dates = ["2025-01-01", "2025-02-01", "2026-01-01", "2026-02-01"]
        values = [100, 110, 105, 115]
        mr = _metrics.monthly_returns(dates, values)
        assert "2025" in mr
        assert "2026" in mr


class TestProfitFactor:
    """Tests for :func:`metrics.profit_factor`."""

    def test_basic(self) -> None:
        trades = [
            {"net_profit": 100}, {"net_profit": 50},
            {"net_profit": -30}, {"net_profit": -20},
        ]
        pf = _metrics.profit_factor(trades)
        assert pf == pytest.approx(3.0, abs=0.01)

    def test_all_profits(self) -> None:
        trades = [{"return_pct": 10}, {"return_pct": 20}]
        assert _metrics.profit_factor(trades) == float("inf")

    def test_all_losses(self) -> None:
        trades = [{"return_pct": -10}, {"return_pct": -20}]
        assert _metrics.profit_factor(trades) == 0.0

    def test_empty(self) -> None:
        assert _metrics.profit_factor([]) == 0.0


class TestExpectancy:
    """Tests for :func:`metrics.expectancy`."""

    def test_basic(self, sample_trades: list[dict[str, Any]]) -> None:
        exp = _metrics.expectancy(sample_trades)
        assert exp == pytest.approx(16600.0, abs=0.01)

    def test_empty(self) -> None:
        assert _metrics.expectancy([]) == 0.0


class TestAverageWin:
    """Tests for :func:`metrics.average_win`."""

    def test_basic(self, sample_trades: list[dict[str, Any]]) -> None:
        aw = _metrics.average_win(sample_trades)
        assert aw == pytest.approx(69500.0, abs=0.01)

    def test_empty(self) -> None:
        assert _metrics.average_win([]) == 0.0


class TestAverageLoss:
    """Tests for :func:`metrics.average_loss`."""

    def test_basic(self, sample_trades: list[dict[str, Any]]) -> None:
        al = _metrics.average_loss(sample_trades)
        assert al == pytest.approx(-28000.0, abs=0.01)

    def test_empty(self) -> None:
        assert _metrics.average_loss([]) == 0.0


class TestLargestWin:
    """Tests for :func:`metrics.largest_win`."""

    def test_basic(self, sample_trades: list[dict[str, Any]]) -> None:
        lw = _metrics.largest_win(sample_trades)
        assert lw == 87000.0

    def test_empty(self) -> None:
        assert _metrics.largest_win([]) == 0.0


class TestLargestLoss:
    """Tests for :func:`metrics.largest_loss`."""

    def test_basic(self, sample_trades: list[dict[str, Any]]) -> None:
        ll = _metrics.largest_loss(sample_trades)
        assert ll == -35000.0

    def test_empty(self) -> None:
        assert _metrics.largest_loss([]) == 0.0


class TestCAGR:
    """Tests for :func:`metrics.cagr`."""

    def test_positive(self) -> None:
        c = _metrics.cagr(1000, 1500, 3)
        assert c == pytest.approx(14.47, abs=0.01)

    def test_negative(self, sample_trades: list[dict[str, Any]]) -> None:
        assert _metrics.cagr(1000, 500, 3) < 0

    def test_zero_initial(self) -> None:
        assert _metrics.cagr(0, 100, 1) == 0.0

    def test_zero_years(self) -> None:
        assert _metrics.cagr(100, 200, 0) == 0.0


class TestValueAtRisk:
    """Tests for :func:`metrics.value_at_risk`."""

    def test_basic(self) -> None:
        returns = [-0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
        var95 = _metrics.value_at_risk(returns, 0.95)
        assert var95 <= 0

    def test_empty(self) -> None:
        assert _metrics.value_at_risk([], 0.95) == 0.0

    def test_all_positive(self) -> None:
        assert _metrics.value_at_risk([1, 2, 3], 0.95) == 1


class TestConditionalVar:
    """Tests for :func:`metrics.conditional_var`."""

    def test_basic(self) -> None:
        returns = [-0.10, -0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
        cvar95 = _metrics.conditional_var(returns, 0.95)
        assert cvar95 <= 0

    def test_empty(self) -> None:
        assert _metrics.conditional_var([], 0.95) == 0.0

    def test_single_value(self) -> None:
        assert _metrics.conditional_var([-0.05], 0.95) == -0.05


class TestVolatility:
    """Tests for :func:`metrics.volatility`."""

    def test_basic(self) -> None:
        returns = [0.01, -0.01, 0.02, -0.02]
        vol = _metrics.volatility(returns)
        assert vol > 0

    def test_empty(self) -> None:
        assert _metrics.volatility([]) == 0.0

    def test_single(self) -> None:
        assert _metrics.volatility([0.01]) == 0.0

    def test_identical(self) -> None:
        assert _metrics.volatility([0.01, 0.01]) == 0.0


class TestDownsideDeviation:
    """Tests for :func:`metrics.downside_deviation`."""

    def test_basic(self) -> None:
        returns = [0.01, -0.02, 0.03, -0.01]
        dd = _metrics.downside_deviation(returns)
        assert dd > 0

    def test_all_positive(self) -> None:
        assert _metrics.downside_deviation([0.01, 0.02]) == 0.0

    def test_empty(self) -> None:
        assert _metrics.downside_deviation([]) == 0.0


class TestBeta:
    """Tests for :func:`metrics.beta`."""

    def test_basic(self) -> None:
        asset = [0.01, 0.02, -0.01, 0.015]
        market = [0.008, 0.015, -0.005, 0.01]
        b = _metrics.beta(asset, market)
        assert b is not None and b > 0

    def test_insufficient_data(self) -> None:
        assert _metrics.beta([0.01], [0.01]) is None

    def test_empty(self) -> None:
        assert _metrics.beta([], []) is None


class TestCorrelation:
    """Tests for :func:`metrics.correlation`."""

    def test_perfect_positive(self) -> None:
        a = [1, 2, 3, 4, 5]
        b = [2, 4, 6, 8, 10]
        c = _metrics.correlation(a, b)
        assert c is not None and c == pytest.approx(1.0, abs=0.01)

    def test_perfect_negative(self) -> None:
        a = [1, 2, 3, 4, 5]
        b = [5, 4, 3, 2, 1]
        c = _metrics.correlation(a, b)
        assert c is not None and c == pytest.approx(-1.0, abs=0.01)

    def test_no_correlation(self) -> None:
        a = [1, 2, 3, 4, 5]
        b = [3, 3, 3, 3, 3]
        c = _metrics.correlation(a, b)
        assert c is None

    def test_insufficient_data(self) -> None:
        assert _metrics.correlation([1], [1]) is None

    def test_empty(self) -> None:
        assert _metrics.correlation([], []) is None


class TestRecoveryFactor:
    """Tests for :func:`metrics.recovery_factor`."""

    def test_basic(self) -> None:
        rf = _metrics.recovery_factor(100000, 50000)
        assert rf is not None and rf == 2.0

    def test_zero_drawdown(self) -> None:
        assert _metrics.recovery_factor(100000, 0) is None

    def test_negative_drawdown(self) -> None:
        assert _metrics.recovery_factor(100000, -1) is None


class TestPortfolioSummary:
    """Tests for :func:`metrics.portfolio_summary`."""

    def test_basic(self) -> None:
        summary = _metrics.portfolio_summary(
            invested=1000000,
            cash=500000,
            current_value=1200000,
            trades=[
                {"return_pct": 5}, {"return_pct": -2},
                {"return_pct": 3}, {"return_pct": -1},
            ],
            equity_curve=[1000000, 1050000, 1100000, 1080000],
            returns=[0.05, -0.02, 0.03, -0.01],
        )
        assert summary["total_capital"] == 1500000.0
        assert summary["invested"] == 1000000.0
        assert summary["cash"] == 500000.0
        assert summary["return_pct"] == 20.0
        assert summary["num_trades"] == 4
        assert "sharpe_ratio" in summary
        assert "max_drawdown" in summary
        assert "value_at_risk_95" in summary

    def test_empty(self) -> None:
        summary = _metrics.portfolio_summary()
        assert summary["total_capital"] == 0.0
        assert summary["num_trades"] == 0

    def test_partial_data(self) -> None:
        summary = _metrics.portfolio_summary(invested=100000)
        assert summary["invested"] == 100000.0
        assert summary["return_pct"] == 0.0


# ======================================================================
# Console reports tests
# ======================================================================


class TestPortfolioReport:
    """Tests for :func:`reports.portfolio_report`."""

    def test_basic(self, sample_summary: dict[str, Any]) -> None:
        report = _reports.portfolio_report(sample_summary)
        assert "Portfolio Summary" in report
        assert "NPR" in report
        assert "Sharpe" in report
        assert "1.2500" in report

    def test_empty(self) -> None:
        report = _reports.portfolio_report({})
        assert "Portfolio Summary" in report

    def test_custom_title(self) -> None:
        report = _reports.portfolio_report({}, "My Report")
        assert "My Report" in report


class TestTradeReport:
    """Tests for :func:`reports.trade_report`."""

    def test_basic(self, sample_trades: list[dict[str, Any]]) -> None:
        report = _reports.trade_report(sample_trades)
        assert "Trade Report" in report
        assert "NABIL" in report
        assert "CHCL" in report

    def test_empty(self) -> None:
        report = _reports.trade_report([])
        assert "0" in report

    def test_many_trades(self) -> None:
        trades = [
            {"symbol": f"S{i}", "return_pct": 1.0, "net_profit": 100}
            for i in range(60)
        ]
        report = _reports.trade_report(trades)
        assert "50" in report


class TestStrategyReport:
    """Tests for :func:`reports.strategy_report`."""

    def test_basic(self) -> None:
        strategies = {
            "Momentum": {"sharpe": 1.5, "win_rate": 65.0},
            "Breakout": {"sharpe": 1.2, "win_rate": 55.0},
        }
        report = _reports.strategy_report(strategies)
        assert "Strategy Comparison" in report
        assert "Momentum" in report
        assert "Breakout" in report
        assert "1.5000" in report

    def test_empty(self) -> None:
        report = _reports.strategy_report({})
        assert "Strategy Comparison" in report

    def test_non_dict_metrics(self) -> None:
        strategies = {"Momentum": 0.5, "Breakout": 0.3}
        report = _reports.strategy_report(strategies)
        assert "Momentum" in report
        assert "0.5" in report


class TestWeightsReport:
    """Tests for :func:`reports.weights_report`."""

    def test_basic(self) -> None:
        weights = {"Momentum": 0.5, "Breakout": 0.3, "Defensive": 0.2}
        report = _reports.weights_report(weights)
        assert "Strategy Weights" in report
        assert "Momentum" in report
        assert "0.5000" in report

    def test_sorted(self) -> None:
        weights = {"A": 0.1, "B": 0.8, "C": 0.1}
        report = _reports.weights_report(weights)
        assert report.index("B") < report.index("A")


class TestRegimeReport:
    """Tests for :func:`reports.regime_report`."""

    def test_basic(self) -> None:
        report = _reports.regime_report("BULL", 85.0)
        assert "Market Regime Report" in report
        assert "BULL" in report
        assert "85.0%" in report

    def test_with_reasons(self) -> None:
        report = _reports.regime_report(
            "BEAR", 75.0, reasons=["Price below MA", "Weak volume"]
        )
        assert "BEAR" in report
        assert "Price below MA" in report

    def test_with_metrics(self) -> None:
        report = _reports.regime_report(
            "BULL", 90.0, metrics={"adx": 30, "rsi": 65}
        )
        assert "Adx" in report or "ADX" in report
        assert "30" in report or "30.0" in report


class TestAllocationReport:
    """Tests for :func:`reports.allocation_report`."""

    def test_basic(self) -> None:
        allocations = [
            {"symbol": "NABIL", "weight": 0.5, "shares": 500, "capital": 500000,
             "expected_return": 0.15, "risk": 0.2, "sector": "Banking"},
            {"symbol": "CHCL", "weight": 0.5, "shares": 500, "capital": 500000,
             "expected_return": 0.12, "risk": 0.18, "sector": "Hydropower"},
        ]
        report = _reports.allocation_report(allocations)
        assert "Portfolio Allocations" in report
        assert "NABIL" in report
        assert "Banking" in report

    def test_empty(self) -> None:
        report = _reports.allocation_report([])
        assert "Portfolio Allocations" in report


class TestExecutiveReport:
    """Tests for :func:`reports.executive_report`."""

    def test_basic(self, sample_summary: dict[str, Any]) -> None:
        report = _reports.executive_report(sample_summary, "BULL", 85.0)
        assert "EXECUTIVE REPORT" in report
        assert "BULL" in report

    def test_with_trades_and_allocations(
        self, sample_summary: dict[str, Any], sample_trades: list[dict[str, Any]]
    ) -> None:
        report = _reports.executive_report(
            sample_summary, "BULL", 85.0,
            trades=sample_trades,
            allocations=[{"symbol": "NABIL", "weight": 0.5, "shares": 500,
                         "capital": 500000, "expected_return": 0.15,
                         "risk": 0.2, "sector": "Banking"}],
        )
        assert "NABIL" in report
        assert "Recent Trades" in report
        assert "Current Allocations" in report


class TestDetailedReport:
    """Tests for :func:`reports.detailed_report`."""

    def test_basic(self, sample_summary: dict[str, Any]) -> None:
        report = _reports.detailed_report(sample_summary)
        assert "DETAILED REPORT" in report

    def test_with_all_data(
        self, sample_summary: dict[str, Any], sample_trades: list[dict[str, Any]]
    ) -> None:
        report = _reports.detailed_report(
            sample_summary,
            regime_label="BULL",
            regime_confidence=85.0,
            regime_reasons=["Strong trend"],
            regime_metrics={"adx": 30.0},
            trades=sample_trades,
            allocations=[{"symbol": "NABIL", "weight": 0.5, "shares": 500,
                         "capital": 500000, "expected_return": 0.15,
                         "risk": 0.2, "sector": "Banking"}],
            strategy_weights={"Momentum": 0.5, "Breakout": 0.3},
            risk_metrics={"VaR_95": 0.02, "Max Drawdown": 15.0},
        )
        assert "DETAILED REPORT" in report
        assert "VaR" in report or "var" in report.lower()


# ======================================================================
# Export tests
# ======================================================================


class TestExportJSON:
    """Tests for :func:`exporter.export_json`."""

    def test_basic(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "test.json")
        result = _exporter.export_json(
            {"key": "value", "num": 42}, path
        )
        assert os.path.exists(result)
        with open(result, encoding="utf-8") as f:
            data = json.load(f)
        assert data["key"] == "value"
        assert data["num"] == 42

    def test_custom_indent(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "indent.json")
        _exporter.export_json({"a": 1}, path, indent=4)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "    " in content  # 4-space indent

    def test_non_serialisable(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "complex.json")
        result = _exporter.export_json(
            {"data": {"nested": object()}}, path
        )
        assert os.path.exists(result)

    def test_empty_dict(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty.json")
        result = _exporter.export_json({}, path)
        with open(result) as f:
            assert json.load(f) == {}


class TestExportCSV:
    """Tests for :func:`exporter.export_csv`."""

    def test_basic(self, temp_dir: str) -> None:
        rows = [
            {"symbol": "NABIL", "weight": 0.5},
            {"symbol": "CHCL", "weight": 0.3},
        ]
        path = os.path.join(temp_dir, "test.csv")
        result = _exporter.export_csv(rows, path)
        with open(result, encoding="utf-8") as f:
            content = f.read()
        assert "symbol" in content
        assert "NABIL" in content
        assert "CHCL" in content

    def test_custom_fieldnames(self, temp_dir: str) -> None:
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        path = os.path.join(temp_dir, "fields.csv")
        _exporter.export_csv(rows, path, fieldnames=["b", "a"])
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # b should appear before a
        b_pos = content.index("b")
        a_pos = content.index("a")
        assert b_pos < a_pos

    def test_empty_rows(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty.csv")
        result = _exporter.export_csv([], path)
        assert os.path.exists(result)


class TestExportHTML:
    """Tests for :func:`exporter.export_html`."""

    def test_basic_text(self, temp_dir: str) -> None:
        sections = [
            {"type": "text", "content": "Hello World"},
        ]
        path = os.path.join(temp_dir, "test.html")
        result = _exporter.export_html("Test Report", sections, path)
        with open(result, encoding="utf-8") as f:
            content = f.read()
        assert "<title>Test Report</title>" in content
        assert "Hello World" in content
        assert "plotly" in content

    def test_table_section(self, temp_dir: str) -> None:
        sections = [
            {
                "type": "table",
                "content": {
                    "headers": ["Col1", "Col2"],
                    "rows": [["A", "1"], ["B", "2"]],
                },
            },
        ]
        path = os.path.join(temp_dir, "table.html")
        _exporter.export_html("Table Test", sections, path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "Col1" in content
        assert "Col2" in content
        assert "<tr>" in content

    def test_metric_section(self, temp_dir: str) -> None:
        sections = [
            {"type": "metric", "content": {"label": "Win Rate", "value": "75%"}},
        ]
        path = os.path.join(temp_dir, "metric.html")
        _exporter.export_html("Metric Test", sections, path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "Win Rate" in content
        assert "75%" in content

    def test_plotly_section(self, temp_dir: str) -> None:
        sections = [
            {
                "type": "plotly",
                "content": {"fig_json": '{"data": [], "layout": {}}'},
            },
        ]
        path = os.path.join(temp_dir, "plotly.html")
        _exporter.export_html("Plotly Test", sections, path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "Plotly.newPlot" in content

    def test_empty_sections(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty.html")
        _exporter.export_html("Empty", [], path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "Empty" in content


class TestExportPDF:
    """Tests for :func:`exporter.export_pdf`."""

    def test_basic(self, temp_dir: str) -> None:
        sections = [
            {"type": "text", "content": "Portfolio Report"},
            {"type": "metric", "content": {"label": "Return", "value": "20%"}},
        ]
        path = os.path.join(temp_dir, "test.pdf")
        result = _exporter.export_pdf("Report", sections, path)
        assert os.path.exists(result)
        with open(result, "rb") as f:
            header = f.read(8)
        assert header[:5] == b"%PDF-"


# ======================================================================
# Dashboard class tests
# ======================================================================


class TestDashboardMetrics:
    """Tests for :class:`Dashboard` metric methods."""

    def test_portfolio_summary(self, dashboard: Dashboard) -> None:
        summary = dashboard.portfolio_summary(
            invested=1000000, cash=500000, current_value=1200000
        )
        assert summary["total_capital"] == 1500000.0

    def test_win_rate(
        self, dashboard: Dashboard, sample_trades: list[dict[str, Any]]
    ) -> None:
        wr = dashboard.win_rate(sample_trades)
        assert wr == 40.0

    def test_sharpe_ratio(
        self, dashboard: Dashboard, sample_returns: list[float]
    ) -> None:
        sr = dashboard.sharpe_ratio(sample_returns)
        assert sr is not None

    def test_max_drawdown(
        self, dashboard: Dashboard, sample_equity_curve: list[float]
    ) -> None:
        mdd = dashboard.max_drawdown(sample_equity_curve)
        assert mdd > 0

    def test_drawdown_curve(
        self, dashboard: Dashboard, sample_equity_curve: list[float]
    ) -> None:
        dd = dashboard.drawdown_curve(sample_equity_curve)
        assert len(dd) == len(sample_equity_curve)

    def test_value_at_risk(
        self, dashboard: Dashboard, sample_returns: list[float]
    ) -> None:
        var95 = dashboard.value_at_risk(sample_returns)
        assert var95 <= 0

    def test_conditional_var(
        self, dashboard: Dashboard, sample_returns: list[float]
    ) -> None:
        cvar95 = dashboard.conditional_var(sample_returns)
        assert cvar95 <= 0

    def test_monthly_returns(
        self,
        dashboard: Dashboard,
        sample_dates: list[str],
        sample_equity_curve: list[float],
    ) -> None:
        mr = dashboard.monthly_returns(sample_dates, sample_equity_curve)
        assert isinstance(mr, dict)


class TestDashboardReports:
    """Tests for :class:`Dashboard` console report methods."""

    def test_portfolio_report(
        self, dashboard: Dashboard, sample_summary: dict[str, Any]
    ) -> None:
        report = dashboard.console_report(
            "portfolio", summary=sample_summary
        )
        assert "Portfolio Summary" in report

    def test_trade_report(
        self, dashboard: Dashboard, sample_trades: list[dict[str, Any]]
    ) -> None:
        report = dashboard.console_report("trade", trades=sample_trades)
        assert "Trade Report" in report

    def test_strategy_report(self, dashboard: Dashboard) -> None:
        report = dashboard.console_report(
            "strategy", strategies={"M": {"s": 1.5}}
        )
        assert "Strategy Comparison" in report

    def test_weights_report(self, dashboard: Dashboard) -> None:
        report = dashboard.console_report(
            "weights", weights={"A": 0.5, "B": 0.5}
        )
        assert "Strategy Weights" in report

    def test_regime_report(self, dashboard: Dashboard) -> None:
        report = dashboard.console_report(
            "regime", regime_label="BULL", confidence=85.0
        )
        assert "BULL" in report

    def test_allocation_report(self, dashboard: Dashboard) -> None:
        report = dashboard.console_report(
            "allocation",
            allocations=[{"symbol": "A", "weight": 1.0, "shares": 100,
                         "capital": 1000, "expected_return": 0.1,
                         "risk": 0.2, "sector": "Banking"}],
        )
        assert "A" in report

    def test_executive_report(
        self, dashboard: Dashboard, sample_summary: dict[str, Any]
    ) -> None:
        report = dashboard.console_report(
            "executive", portfolio_summary=sample_summary
        )
        assert "EXECUTIVE REPORT" in report

    def test_detailed_report(
        self, dashboard: Dashboard, sample_summary: dict[str, Any]
    ) -> None:
        report = dashboard.console_report(
            "detailed", portfolio_summary=sample_summary
        )
        assert "DETAILED REPORT" in report

    def test_invalid_report_type(self, dashboard: Dashboard) -> None:
        with pytest.raises(ValueError, match="Unknown report type"):
            dashboard.console_report("invalid_type")


class TestDashboardExport:
    """Tests for :class:`Dashboard` export methods."""

    def test_export_json(self, dashboard: Dashboard, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "out.json")
        result = dashboard.export(
            "json", {"key": "value"}, path
        )
        assert os.path.exists(result)
        with open(result) as f:
            data = json.load(f)
        assert data == {"key": "value"}

    def test_export_csv(self, dashboard: Dashboard, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "out.csv")
        result = dashboard.export(
            "csv", [{"a": 1}, {"a": 2}], path
        )
        assert os.path.exists(result)

    def test_export_html(self, dashboard: Dashboard, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "out.html")
        result = dashboard.export(
            "html",
            {"title": "Test", "sections": [{"type": "text", "content": "Hi"}]},
            path,
        )
        assert os.path.exists(result)
        with open(result) as f:
            assert "Hi" in f.read()

    def test_export_pdf(self, dashboard: Dashboard, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "out.pdf")
        result = dashboard.export(
            "pdf",
            {"title": "Test", "sections": [{"type": "text", "content": "Hi"}]},
            path,
        )
        assert os.path.exists(result)

    def test_export_invalid_type(self, dashboard: Dashboard, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "out.txt")
        with pytest.raises(ValueError, match="Unknown export type"):
            dashboard.export("txt", {}, path)

    def test_export_json_invalid_data(
        self, dashboard: Dashboard, temp_dir: str
    ) -> None:
        path = os.path.join(temp_dir, "out.json")
        with pytest.raises(ValueError, match="JSON export requires a dict"):
            dashboard.export("json", [1, 2, 3], path)

    def test_export_csv_invalid_data(
        self, dashboard: Dashboard, temp_dir: str
    ) -> None:
        path = os.path.join(temp_dir, "out.csv")
        with pytest.raises(ValueError, match="CSV export requires a list"):
            dashboard.export("csv", {}, path)

    def test_export_html_invalid_data(
        self, dashboard: Dashboard, temp_dir: str
    ) -> None:
        path = os.path.join(temp_dir, "out.html")
        with pytest.raises(ValueError, match="HTML export requires a dict"):
            dashboard.export("html", [], path)

    def test_export_pdf_invalid_data(
        self, dashboard: Dashboard, temp_dir: str
    ) -> None:
        path = os.path.join(temp_dir, "out.pdf")
        with pytest.raises(ValueError, match="PDF export requires a dict"):
            dashboard.export("pdf", [], path)


class TestDashboardCharts:
    """Tests for :class:`Dashboard` chart methods."""

    def test_allocation_pie(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.allocation_pie(
                ["A", "B", "C"], [50, 30, 20]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_equity_curve_chart(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.equity_curve_chart(
                ["2025-01", "2025-02"], [100, 110]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_drawdown_chart(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.drawdown_chart(
                ["2025-01", "2025-02"], [100, 90]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_monthly_returns_heatmap(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.monthly_returns_heatmap(
                {"2025": {"01": 5.0, "02": -2.0}}
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_trade_distribution(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.trade_distribution([1, -2, 3, -1, 0])
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_win_loss_pie(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.win_loss_pie(wins=10, losses=5, breakeven=1)
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_zero_trades_win_loss(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.win_loss_pie(wins=0, losses=0)
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_strategy_comparison(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.strategy_comparison(
                ["M", "B"], {"Sharpe": [1.5, 1.2]}
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_monte_carlo_paths(self, dashboard: Dashboard) -> None:
        try:
            curves = [[100, 110, 105], [100, 95, 105]]
            fig = dashboard.monte_carlo_paths(
                curves, [100, 110, 115], [100, 90, 85], [100, 100, 100]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_monte_carlo_confidence_bands(self, dashboard: Dashboard) -> None:
        try:
            curves = [[100, 110, 105], [100, 95, 100]]
            fig = dashboard.monte_carlo_confidence_bands(
                curves, {"p5": 90, "p50": 100, "p95": 110}
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_efficient_frontier(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.efficient_frontier(
                [0.1, 0.2, 0.3], [0.05, 0.10, 0.15],
                optimal_risk=0.2, optimal_return=0.10
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_portfolio_weights(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.portfolio_weights(
                ["A", "B"], [0.6, 0.4]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_risk_contribution_pie(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.risk_contribution_pie(
                ["Banking", "Hydro"], [0.6, 0.4]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_regime_timeline(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.regime_timeline([
                {"date": "2025-01", "regime": "BULL"},
                {"date": "2025-02", "regime": "BEAR"},
            ])
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_optimisation_results(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.optimisation_results(
                [0.1, 0.2, 0.3], [0.05, 0.10, 0.15],
                scores=[50, 80, 70], best_idx=1
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_adaptive_strategy_radar(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.adaptive_strategy_radar(
                ["Momentum", "Breakout"], [0.6, 0.4]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_risk_gauge(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.risk_gauge(value=65.0)
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_parameter_heatmap(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.parameter_heatmap(
                ["10", "20"], ["50", "100"], [[0.5, 0.6], [0.7, 0.8]]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_walk_forward_performance(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.walk_forward_performance(
                ["W1", "W2", "W3"],
                [0.8, 0.7, 0.9],
                [0.6, 0.5, 0.7],
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_recommendation_gauge(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.recommendation_gauge(
                confidence=85.0, signal="BUY"
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")

    def test_sector_treemap(self, dashboard: Dashboard) -> None:
        try:
            fig = dashboard.sector_treemap(
                ["Banking", "Hydro", "Insurance"], [40, 30, 20]
            )
            assert fig is not None
        except ImportError:
            pytest.skip("Plotly not installed")


# ======================================================================
# Edge cases and error handling
# ======================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_trade_report_empty(
        self, sample_trades: list[dict[str, Any]]
    ) -> None:
        report = _reports.trade_report(sample_trades)
        assert isinstance(report, str)

    def test_report_without_symbol(
        self, sample_trades: list[dict[str, Any]]
    ) -> None:
        sample_trades[0].pop("symbol")
        report = _reports.trade_report(sample_trades)
        assert isinstance(report, str)

    def test_export_json_empty_path(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty_test.json")
        _exporter.export_json({"a": 1}, path)
        assert os.path.exists(path)

    def test_export_csv_fieldnames_empty(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty_fn.csv")
        _exporter.export_csv([{"a": 1}], path, fieldnames=[])
        assert os.path.exists(path)

    def test_export_html_no_sections(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "no_sec.html")
        _exporter.export_html("Test", [], path)
        assert os.path.exists(path)

    def test_export_pdf_empty_content(self, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty.pdf")
        _exporter.export_pdf("Empty", [], path)
        assert os.path.exists(path)

    def test_sortino_ratio_empty(self) -> None:
        assert _metrics.sortino_ratio([]) is None

    def test_sortino_ratio_single(self) -> None:
        assert _metrics.sortino_ratio([0.01]) is None

    def test_calmar_ratio_zero_drawdown(self) -> None:
        assert _metrics.calmar_ratio(10, 0) is None

    def test_monthly_returns_invalid_date(self) -> None:
        dates = ["bad-date", "2025-01-02"]
        values = [100, 110]
        mr = _metrics.monthly_returns(dates, values)
        assert len(mr) == 0

    def test_monthly_returns_short_date(self) -> None:
        dates = ["2025", "2025-01-15"]
        values = [100, 110]
        mr = _metrics.monthly_returns(dates, values)
        assert len(mr) == 0  # only one valid date — no return to compute

    def test_volatility_identical_returns(self) -> None:
        assert _metrics.volatility([0.01, 0.01, 0.01]) == 0.0

    def test_beta_empty_asset(self) -> None:
        assert _metrics.beta([], [0.01, 0.02]) is None

    def test_beta_empty_market(self) -> None:
        assert _metrics.beta([0.01, 0.02], []) is None

    def test_beta_different_lengths(self) -> None:
        b = _metrics.beta([0.01, 0.02, 0.03], [0.01, 0.02])
        assert b is not None

    def test_correlation_zero_variance(self) -> None:
        assert _metrics.correlation([1, 1, 1], [1, 2, 3]) is None

    def test_correlation_different_lengths(self) -> None:
        c = _metrics.correlation([1, 2, 3], [1, 2])
        assert c is not None


class TestDashboardEdgeCases:
    """Tests for :class:`Dashboard` edge cases."""

    def test_empty_report_kwargs(self, dashboard: Dashboard) -> None:
        with pytest.raises(ValueError):
            dashboard.console_report("unknown_type")

    def test_export_empty_json(self, dashboard: Dashboard, temp_dir: str) -> None:
        path = os.path.join(temp_dir, "empty.json")
        result = dashboard.export("json", {}, path)
        assert os.path.exists(result)

    def test_export_missing_file_permissions(
        self, dashboard: Dashboard, temp_dir: str
    ) -> None:
        # Should handle permission errors gracefully
        invalid_path = os.path.join(
            temp_dir, "nested", "nonexistent", "out.json"
        )
        with pytest.raises(OSError):
            dashboard.export("json", {"key": "val"}, invalid_path)
