"""Pure performance metrics for backtest trade results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import fmean, pstdev
from typing import Any

Trade = Mapping[str, Any]
DEFAULT_METRICS: dict[str, float | int] = {
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "breakeven_trades": 0,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "average_win": 0.0,
    "average_loss": 0.0,
    "expectancy": 0.0,
}


def _trade_returns(trades: Sequence[Trade]) -> list[float]:
    """Extract numeric percentage returns from trade records."""
    return [float(trade.get("return_pct", 0.0)) for trade in trades]


def calculate_win_rate(trades: Sequence[Trade]) -> float:
    """Calculate the percentage of trades with a positive return.

    Args:
        trades: Trade records containing optional ``return_pct`` values.

    Returns:
        Winning trades as a percentage of all trades, or ``0.0`` when empty.
    """
    if not trades:
        return 0.0
    returns = _trade_returns(trades)
    return sum(value > 0 for value in returns) / len(returns) * 100


def calculate_profit_factor(trades: Sequence[Trade]) -> float:
    """Calculate gross profit divided by absolute gross loss.

    Args:
        trades: Trade records containing optional ``return_pct`` values.

    Returns:
        Profit factor, or ``0.0`` when there is no loss to compare against.
    """
    returns = _trade_returns(trades)
    gross_profit = sum(value for value in returns if value > 0)
    gross_loss = abs(sum(value for value in returns if value < 0))
    return gross_profit / gross_loss if gross_loss else 0.0


def calculate_average_win(trades: Sequence[Trade]) -> float:
    """Calculate the mean positive trade return.

    Args:
        trades: Trade records containing optional ``return_pct`` values.

    Returns:
        Average winning return, or ``0.0`` when there are no winning trades.
    """
    wins = [value for value in _trade_returns(trades) if value > 0]
    return fmean(wins) if wins else 0.0


def calculate_average_loss(trades: Sequence[Trade]) -> float:
    """Calculate the mean negative trade return.

    Args:
        trades: Trade records containing optional ``return_pct`` values.

    Returns:
        Average losing return, or ``0.0`` when there are no losing trades.
    """
    losses = [value for value in _trade_returns(trades) if value < 0]
    return fmean(losses) if losses else 0.0


def calculate_expectancy(trades: Sequence[Trade]) -> float:
    """Calculate expected return per trade.

    Args:
        trades: Trade records containing optional ``return_pct`` values.

    Returns:
        Mean trade return, or ``0.0`` when no trades are supplied.
    """
    returns = _trade_returns(trades)
    return fmean(returns) if returns else 0.0


def calculate_max_drawdown(equity_curve: Sequence[float]) -> float:
    """Calculate the largest peak-to-trough drawdown percentage.

    Args:
        equity_curve: Ordered account-equity values.

    Returns:
        Maximum drawdown as a positive percentage, or ``0.0`` when unavailable.
    """
    peak: float | None = None
    max_drawdown = 0.0
    for equity in equity_curve:
        value = float(equity)
        if peak is None or value > peak:
            peak = value
        if peak and value < peak:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
    return max_drawdown


def calculate_sharpe_ratio(
    returns: Sequence[float], risk_free_rate: float = 0
) -> float:
    """Calculate the non-annualized Sharpe ratio using population volatility.

    Args:
        returns: Per-period returns expressed in a consistent unit.
        risk_free_rate: Per-period risk-free return in the same unit.

    Returns:
        Sharpe ratio, or ``0.0`` when volatility cannot be calculated.
    """
    excess_returns = [float(value) - risk_free_rate for value in returns]
    if len(excess_returns) < 2:
        return 0.0
    volatility = pstdev(excess_returns)
    return fmean(excess_returns) / volatility if volatility else 0.0


def calculate_total_return(initial_capital: float, final_capital: float) -> float:
    """Calculate total portfolio return as a percentage.

    Args:
        initial_capital: Starting portfolio value.
        final_capital: Ending portfolio value.

    Returns:
        Percentage return, or ``0.0`` when initial capital is zero.
    """
    if initial_capital == 0:
        return 0.0
    return (final_capital - initial_capital) / initial_capital * 100


def calculate_cagr(initial_capital: float, final_capital: float, years: float) -> float:
    """Calculate compound annual growth rate as a percentage.

    Args:
        initial_capital: Starting portfolio value.
        final_capital: Ending portfolio value.
        years: Investment duration in years.

    Returns:
        CAGR percentage, or ``0.0`` for invalid capital or duration values.
    """
    if initial_capital <= 0 or final_capital < 0 or years <= 0:
        return 0.0
    return ((final_capital / initial_capital) ** (1 / years) - 1) * 100


def calculate_trade_statistics(trades: Sequence[Trade]) -> dict[str, float | int]:
    """Compile commonly used statistics for a collection of trades.

    Args:
        trades: Trade records containing optional ``return_pct`` values.

    Returns:
        Counts and aggregate return metrics with safe zero-value defaults.
    """
    if not trades:
        return DEFAULT_METRICS.copy()

    returns = _trade_returns(trades)
    return {
        "total_trades": len(returns),
        "winning_trades": sum(value > 0 for value in returns),
        "losing_trades": sum(value < 0 for value in returns),
        "breakeven_trades": sum(value == 0 for value in returns),
        "win_rate": calculate_win_rate(trades),
        "profit_factor": calculate_profit_factor(trades),
        "average_win": calculate_average_win(trades),
        "average_loss": calculate_average_loss(trades),
        "expectancy": calculate_expectancy(trades),
    }
