"""Tests for backtest orchestration layer and report generation."""

from pathlib import Path

import pandas as pd
import pytest

from src.backtest.engine import backtest, run_backtest
from src.backtest.report import generate_report


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """Create a temporary CSV file with historical market data."""
    dates = pd.date_range("2024-01-01", periods=60)
    data = []
    price = 100.0
    for i, dt in enumerate(dates):
        # Create a trending price curve to trigger signals
        price += 1.5 if i % 2 == 0 else -0.5
        data.append(
            {
                "Date": dt,
                "Open": price - 1,
                "High": price + 5,
                "Low": price - 2,
                "Close": price,
                "Volume": 1000 + i * 10,
            }
        )
    df = pd.DataFrame(data)
    csv_file = tmp_path / "TEST_STOCK.csv"
    df.to_csv(csv_file, index=False)
    return csv_file


def test_run_backtest_returns_expected_structure(sample_csv: Path) -> None:
    """run_backtest orchestrates data loading, simulation, metrics, and report."""
    result = run_backtest(str(sample_csv), commission=0.001, slippage=0.005)

    assert isinstance(result, dict)
    assert "trades" in result
    assert "metrics" in result
    assert "report" in result
    assert "symbol" in result
    assert "candles" in result
    assert "total_trades" in result
    assert "history" in result

    assert result["symbol"] == str(sample_csv)
    assert result["candles"] == 60
    assert isinstance(result["trades"], list)
    assert isinstance(result["metrics"], dict)
    assert isinstance(result["report"], dict)
    assert "summary" in result["report"]


def test_legacy_backtest_entry_point(sample_csv: Path) -> None:
    """Legacy backtest() wrapper returns identical payload structure."""
    result = backtest(str(sample_csv))

    assert "trades" in result
    assert "metrics" in result
    assert "report" in result
    assert "history" in result
    assert result["history"] is result["trades"]


def test_generate_report_formatting() -> None:
    """generate_report produces expected text summary and metric attributes."""
    trades = [{"return_pct": 5.0}, {"return_pct": -2.0}]
    metrics = {
        "total_trades": 2,
        "winning_trades": 1,
        "losing_trades": 1,
        "win_rate": 50.0,
        "profit_factor": 2.5,
        "expectancy": 1.5,
    }

    report = generate_report(trades, metrics, symbol="AAPL")

    assert report["symbol"] == "AAPL"
    assert report["total_trades"] == 2
    assert report["winning_trades"] == 1
    assert report["losing_trades"] == 1
    assert report["win_rate"] == 50.0
    assert report["profit_factor"] == 2.5
    assert "Backtest Report Summary for AAPL" in report["summary"]
    assert "Win Rate        : 50.00%" in report["summary"]
