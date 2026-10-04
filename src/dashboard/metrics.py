"""Centralised metrics calculations for the NEPSE Quant Engine Dashboard.

Provides portfolio, performance, risk, and trade metrics used by the
chart generators, report generators, and export functions.
"""

from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Any


# ======================================================================
# Portfolio metrics
# ======================================================================


def total_capital(invested: float, cash: float) -> float:
    """Calculate total portfolio capital.

    Args:
        invested: Total invested capital.
        cash: Remaining cash.

    Returns:
        Sum of invested and cash.
    """
    return round(invested + cash, 10)


def portfolio_return(initial: float, current: float) -> float:
    """Calculate portfolio return as a percentage.

    Args:
        initial: Starting portfolio value.
        current: Current portfolio value.

    Returns:
        Percentage return.
    """
    if initial == 0:
        return 0.0
    return ((current - initial) / initial) * 100.0


def total_pnl(invested: float, current_value: float) -> float:
    """Calculate total profit/loss.

    Args:
        invested: Total amount invested.
        current_value: Current portfolio value.

    Returns:
        Profit or loss amount.
    """
    return current_value - invested


def win_rate(trades: list[dict[str, Any]]) -> float:
    """Calculate the percentage of profitable trades.

    Args:
        trades: List of trade dicts containing ``return_pct``.

    Returns:
        Win rate as a percentage (0–100).
    """
    if not trades:
        return 0.0
    wins = sum(
        1
        for t in trades
        if float(t.get("net_profit", t.get("return_pct", 0))) > 0
    )
    return (wins / len(trades)) * 100.0


def sharpe_ratio(
    returns: list[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float | None:
    """Calculate the annualised Sharpe ratio.

    Args:
        returns: List of per-period returns.
        risk_free_rate: Per-period risk-free rate.
        periods_per_year: Number of periods per year for annualisation.

    Returns:
        Annualised Sharpe ratio, or ``None`` if insufficient data.
    """
    if len(returns) < 2:
        return None
    excess = [r - risk_free_rate for r in returns]
    mean_r = fmean(excess)
    std_r = pstdev(excess)
    if std_r == 0:
        return None
    return (mean_r / std_r) * math.sqrt(periods_per_year)


def sortino_ratio(
    returns: list[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float | None:
    """Calculate the annualised Sortino ratio.

    Args:
        returns: List of per-period returns.
        risk_free_rate: Per-period risk-free rate.
        periods_per_year: Number of periods per year for annualisation.

    Returns:
        Annualised Sortino ratio, or ``None`` if insufficient data.
    """
    if len(returns) < 2:
        return None
    excess = [r - risk_free_rate for r in returns]
    mean_r = fmean(excess)
    downside = [min(0.0, r) for r in excess]
    semi_var = fmean([d ** 2 for d in downside])
    if semi_var == 0:
        return None
    downside_std = math.sqrt(semi_var)
    return (mean_r / downside_std) * math.sqrt(periods_per_year)


def calmar_ratio(
    total_return_pct: float,
    max_drawdown_pct: float,
) -> float | None:
    """Calculate the Calmar ratio.

    Args:
        total_return_pct: Total portfolio return percentage.
        max_drawdown_pct: Maximum drawdown percentage.

    Returns:
        Calmar ratio, or ``None`` if drawdown is zero.
    """
    if max_drawdown_pct <= 0:
        return None
    return total_return_pct / max_drawdown_pct


def max_drawdown(equity_curve: list[float]) -> float:
    """Calculate the maximum peak-to-trough drawdown as a percentage.

    Args:
        equity_curve: Ordered list of equity values.

    Returns:
        Maximum drawdown as a positive percentage.
    """
    if not equity_curve:
        return 0.0
    peak: float | None = None
    mdd = 0.0
    for value in equity_curve:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0 and value < peak:
            dd = (peak - value) / peak * 100.0
            if dd > mdd:
                mdd = dd
    return mdd


def drawdown_curve(equity_curve: list[float]) -> list[float]:
    """Calculate the drawdown curve from an equity curve.

    Args:
        equity_curve: Ordered list of equity values.

    Returns:
        List of drawdown percentages (positive values).
    """
    if not equity_curve:
        return []
    peak: float | None = None
    dd_curve: list[float] = []
    for value in equity_curve:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            dd = max(0.0, (peak - value) / peak * 100.0)
            dd_curve.append(dd)
        else:
            dd_curve.append(0.0)
    return dd_curve


def monthly_returns(dates: list[str], values: list[float]) -> dict[str, dict[str, float]]:
    """Calculate monthly returns from date-value pairs.

    Iterates through consecutive date-value pairs.  When consecutive
    entries fall in different year-month periods, the return is
    recorded under the later month.  When multiple entries fall in the
    same month, the last computed return overwrites earlier ones.

    Date strings must be at least 7 characters long and have a 4-digit
    year and 2-digit month at positions 0-3 and 5-6 respectively.

    Args:
        dates: List of date strings (YYYY-MM-DD format).
        values: List of portfolio values.

    Returns:
        Nested dict: ``{year: {month: return_pct}}``.
    """
    monthly: dict[str, dict[str, float]] = {}
    prev_val: float | None = None

    for date_str, val in zip(dates, values):
        if len(date_str) < 7:
            continue

        year = date_str[:4]
        month = date_str[5:7]

        # Validate extracted year and month are numeric
        if not year.isdigit() or not month.isdigit():
            continue

        if prev_val is not None and prev_val > 0:
            ret = ((val - prev_val) / prev_val) * 100.0
            if year not in monthly:
                monthly[year] = {}
            monthly[year][month] = ret

        prev_val = val

    return monthly


# ======================================================================
# Performance metrics
# ======================================================================


def profit_factor(trades: list[dict[str, Any]]) -> float:
    """Calculate profit factor from a list of trades.

    Args:
        trades: List of trade dicts with ``net_profit`` or ``return_pct``.

    Returns:
        Profit factor (gross profit / gross loss).
    """
    profits = []
    losses = []
    for t in trades:
        pnl = float(t.get("net_profit", t.get("return_pct", 0)))
        if pnl > 0:
            profits.append(pnl)
        elif pnl < 0:
            losses.append(abs(pnl))

    gross_profit = sum(profits)
    gross_loss = sum(losses)
    if gross_loss == 0:
        return 0.0 if gross_profit == 0 else float("inf")
    return gross_profit / gross_loss


def expectancy(trades: list[dict[str, Any]]) -> float:
    """Calculate expected return per trade.

    Args:
        trades: List of trade dicts with ``net_profit`` or ``return_pct``.

    Returns:
        Mean trade return.
    """
    if not trades:
        return 0.0
    returns = [float(t.get("net_profit", t.get("return_pct", 0))) for t in trades]
    return fmean(returns)


def average_win(trades: list[dict[str, Any]]) -> float:
    """Calculate average winning trade return.

    Args:
        trades: List of trade dicts.

    Returns:
        Mean positive trade return.
    """
    wins = [
        float(t.get("net_profit", t.get("return_pct", 0)))
        for t in trades
        if float(t.get("net_profit", t.get("return_pct", 0))) > 0
    ]
    return fmean(wins) if wins else 0.0


def average_loss(trades: list[dict[str, Any]]) -> float:
    """Calculate average losing trade return.

    Args:
        trades: List of trade dicts.

    Returns:
        Mean negative trade return.
    """
    losses = [
        float(t.get("net_profit", t.get("return_pct", 0)))
        for t in trades
        if float(t.get("net_profit", t.get("return_pct", 0))) < 0
    ]
    return fmean(losses) if losses else 0.0


def largest_win(trades: list[dict[str, Any]]) -> float:
    """Find the largest winning trade.

    Args:
        trades: List of trade dicts.

    Returns:
        Maximum positive trade return, or ``0.0``.
    """
    wins = [
        float(t.get("net_profit", t.get("return_pct", 0)))
        for t in trades
        if float(t.get("net_profit", t.get("return_pct", 0))) > 0
    ]
    return max(wins) if wins else 0.0


def largest_loss(trades: list[dict[str, Any]]) -> float:
    """Find the largest losing trade.

    Args:
        trades: List of trade dicts.

    Returns:
        Maximum negative trade return (most negative), or ``0.0``.
    """
    losses = [
        float(t.get("net_profit", t.get("return_pct", 0)))
        for t in trades
        if float(t.get("net_profit", t.get("return_pct", 0))) < 0
    ]
    return min(losses) if losses else 0.0


def cagr(initial: float, final: float, years: float) -> float:
    """Calculate compound annual growth rate.

    Args:
        initial: Starting value.
        final: Ending value.
        years: Time period in years.

    Returns:
        CAGR as a percentage.
    """
    if initial <= 0 or final < 0 or years <= 0:
        return 0.0
    return ((final / initial) ** (1.0 / years) - 1.0) * 100.0


# ======================================================================
# Risk metrics
# ======================================================================


def value_at_risk(
    returns: list[float],
    confidence: float = 0.95,
) -> float:
    """Calculate Value at Risk at a given confidence level.

    Args:
        returns: List of portfolio returns.
        confidence: Confidence level (e.g. 0.95 = 95%).

    Returns:
        VaR value (negative number representing potential loss).
    """
    if not returns:
        return 0.0
    sorted_ret = sorted(returns)
    idx = max(0, int(len(sorted_ret) * (1 - confidence)))
    return sorted_ret[idx] if idx < len(sorted_ret) else 0.0


def conditional_var(
    returns: list[float],
    confidence: float = 0.95,
) -> float:
    """Calculate Conditional VaR (Expected Shortfall).

    Args:
        returns: List of portfolio returns.
        confidence: Confidence level.

    Returns:
        CVaR value (mean of worst tail returns).
    """
    if not returns:
        return 0.0
    sorted_ret = sorted(returns)
    idx = max(0, int(len(sorted_ret) * (1 - confidence)))
    tail = sorted_ret[: idx + 1]
    return fmean(tail) if tail else 0.0


def volatility(returns: list[float]) -> float:
    """Calculate the standard deviation of returns.

    Args:
        returns: List of returns.

    Returns:
        Population standard deviation, or ``0.0``.
    """
    if len(returns) < 2:
        return 0.0
    return pstdev(returns)


def downside_deviation(returns: list[float]) -> float:
    """Calculate the downside deviation (semi-deviation from zero).

    Args:
        returns: List of returns.

    Returns:
        Downside standard deviation.
    """
    if not returns:
        return 0.0
    negative = [min(0.0, r) for r in returns]
    return math.sqrt(fmean([d ** 2 for d in negative]))


def beta(returns: list[float], market_returns: list[float]) -> float | None:
    """Calculate beta (systematic risk) relative to a market benchmark.

    Args:
        returns: Asset returns.
        market_returns: Market benchmark returns.

    Returns:
        Beta value, or ``None`` if insufficient data.
    """
    if len(returns) < 2 or len(market_returns) < 2:
        return None
    n = min(len(returns), len(market_returns))
    r_asset = returns[:n]
    r_market = market_returns[:n]

    cov = fmean([(a - fmean(r_asset)) * (m - fmean(r_market)) for a, m in zip(r_asset, r_market)])
    var_m = pstdev(r_market) ** 2
    if var_m == 0:
        return None
    return cov / var_m


def correlation(
    returns_a: list[float],
    returns_b: list[float],
) -> float | None:
    """Calculate the Pearson correlation coefficient between two return series.

    Args:
        returns_a: First return series.
        returns_b: Second return series.

    Returns:
        Correlation coefficient (-1 to 1), or ``None`` if insufficient data.
    """
    if len(returns_a) < 2 or len(returns_b) < 2:
        return None
    n = min(len(returns_a), len(returns_b))
    ra = returns_a[:n]
    rb = returns_b[:n]

    mean_a = fmean(ra)
    mean_b = fmean(rb)
    dev_a = [a - mean_a for a in ra]
    dev_b = [b - mean_b for b in rb]

    cov = fmean([a * b for a, b in zip(dev_a, dev_b)])
    std_a = pstdev(ra) if len(ra) > 1 else 0.0
    std_b = pstdev(rb) if len(rb) > 1 else 0.0

    if std_a == 0 or std_b == 0:
        return None
    return cov / (std_a * std_b)


def recovery_factor(net_profit: float, max_drawdown_abs: float) -> float | None:
    """Calculate the recovery factor.

    Args:
        net_profit: Total net profit.
        max_drawdown_abs: Maximum absolute drawdown value.

    Returns:
        Recovery factor, or ``None`` if drawdown is zero.
    """
    if max_drawdown_abs <= 0:
        return None
    return net_profit / max_drawdown_abs


# ======================================================================
# Summary helpers
# ======================================================================


def portfolio_summary(
    invested: float = 0.0,
    cash: float = 0.0,
    current_value: float = 0.0,
    trades: list[dict[str, Any]] | None = None,
    equity_curve: list[float] | None = None,
    returns: list[float] | None = None,
) -> dict[str, Any]:
    """Generate a comprehensive portfolio summary dict.

    Args:
        invested: Total amount invested.
        cash: Remaining cash.
        current_value: Current portfolio value.
        trades: List of trade dicts.
        equity_curve: Equity curve values.
        returns: List of returns.

    Returns:
        Dictionary with all portfolio metrics.
    """
    trades_list = trades or []
    equity_list = equity_curve or []
    returns_list = returns or []

    total = total_capital(invested, cash)
    ret = portfolio_return(invested, current_value) if invested and current_value > 0 else 0.0
    pnl = total_pnl(invested, current_value)
    wr = win_rate(trades_list)
    sr = sharpe_ratio(returns_list) if len(returns_list) >= 2 else None
    mdd = max_drawdown(equity_list) if equity_list else 0.0
    pf = profit_factor(trades_list) if trades_list else 0.0
    exp_val = expectancy(trades_list) if trades_list else 0.0
    avg_w = average_win(trades_list) if trades_list else 0.0
    avg_l = average_loss(trades_list) if trades_list else 0.0
    lw = largest_win(trades_list) if trades_list else 0.0
    ll = largest_loss(trades_list) if trades_list else 0.0
    var95 = value_at_risk(returns_list) if returns_list else 0.0
    cvar95 = conditional_var(returns_list) if returns_list else 0.0
    vol = volatility(returns_list) if len(returns_list) >= 2 else 0.0
    dd = downside_deviation(returns_list) if returns_list else 0.0
    sor = sortino_ratio(returns_list) if len(returns_list) >= 2 else None
    car = calmar_ratio(ret, mdd) if mdd > 0 else None
    rf = recovery_factor(pnl, mdd) if mdd > 0 else None

    return {
        "total_capital": total,
        "invested": invested,
        "cash": cash,
        "current_value": current_value,
        "return_pct": ret,
        "total_pnl": pnl,
        "win_rate": wr,
        "sharpe_ratio": sr,
        "sortino_ratio": sor,
        "calmar_ratio": car,
        "max_drawdown": mdd,
        "profit_factor": pf,
        "expectancy": exp_val,
        "average_win": avg_w,
        "average_loss": avg_l,
        "largest_win": lw,
        "largest_loss": ll,
        "value_at_risk_95": var95,
        "conditional_var_95": cvar95,
        "volatility": vol,
        "downside_deviation": dd,
        "recovery_factor": rf,
        "num_trades": len(trades_list),
    }
