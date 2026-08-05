"""Advanced performance analytics for the institutional backtesting engine.

Expands beyond basic metrics (Part 9.10) with rolling Sharpe/Sortino/
CAGR/alpha/beta/drawdown, recovery factor, MAR ratio, ulcer index,
omega ratio, gain/loss ratio, SQN, expectancy, trade duration,
exposure time, and holding-period distributions.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import fmean, pstdev
from typing import Any

import numpy as np
import pandas as pd

from src.backtesting.models import Trade

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Metric computation helpers
# ═══════════════════════════════════════════════════════════════════


def rolling_series(
    values: list[float],
    window: int,
    fn: Any,
) -> list[float | None]:
    """Apply *fn* to each sliding window of *values*.

    Args:
        values: Input series.
        window: Window length (>= 1).
        fn: Callable taking a list and returning a scalar.

    Returns:
        A list with ``None`` for the first ``window - 1`` entries.
    """
    if window < 1:
        raise ValueError("window must be at least 1.")
    out: list[float | None] = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(None)
            continue
        out.append(fn(values[i - window + 1: i + 1]))
    return out


def drawdown_series(equity: list[float]) -> list[float]:
    """Return percentage drawdown at each equity point (0 = none)."""
    peak: float | None = None
    out: list[float] = []
    for value in equity:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            out.append((peak - value) / peak * 100.0)
        else:
            out.append(0.0)
    return out


def max_drawdown(equity: list[float]) -> float:
    """Return maximum percentage drawdown."""
    return max(drawdown_series(equity), default=0.0)


def annualized_volatility(returns: list[float], periods: int) -> float:
    """Annualised standard deviation of period returns."""
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * math.sqrt(periods)


def cagr(initial: float, final: float, periods: int, periods_per_year: int) -> float:
    """Compound annual growth rate as a decimal (0.1 = 10%)."""
    if initial <= 0 or periods <= 0:
        return 0.0
    years = periods / periods_per_year
    if years <= 0:
        return 0.0
    return (final / initial) ** (1 / years) - 1.0


def sharpe_ratio(returns: list[float], periods: int, risk_free: float = 0.0) -> float:
    """Annualised Sharpe ratio from period returns."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free for r in returns]
    sd = pstdev(excess)
    if sd == 0:
        return 0.0
    return fmean(excess) / sd * math.sqrt(periods)


def sortino_ratio(returns: list[float], periods: int, risk_free: float = 0.0) -> float:
    """Annualised Sortino ratio using downside deviation."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free for r in returns]
    downside = [min(r, 0.0) for r in excess]
    semi_var = fmean(r * r for r in downside)
    if semi_var <= 0:
        return 0.0
    return fmean(excess) / math.sqrt(semi_var) * math.sqrt(periods)


def rolling_sharpe(returns: list[float], window: int, periods: int) -> list[float | None]:
    """Rolling annualised Sharpe over sliding windows."""
    return rolling_series(returns, window, lambda w: sharpe_ratio(w, periods))


def rolling_sortino(returns: list[float], window: int, periods: int) -> list[float | None]:
    """Rolling annualised Sortino over sliding windows."""
    return rolling_series(returns, window, lambda w: sortino_ratio(w, periods))


def rolling_drawdown(equity: list[float], window: int) -> list[float | None]:
    """Rolling maximum drawdown over sliding windows."""
    return rolling_series(equity, window, max_drawdown)


def alpha_beta(
    returns: list[float],
    benchmark_returns: list[float],
    periods: int,
    risk_free: float = 0.0,
) -> tuple[float, float]:
    """Compute (alpha, beta) vs a benchmark.

    Beta is the covariance of returns with the benchmark divided by
    the benchmark variance.  Alpha is the intercept annualised.
    """
    n = min(len(returns), len(benchmark_returns))
    if n < 2:
        return 0.0, 0.0
    r = returns[:n]
    b = benchmark_returns[:n]
    b_mean = fmean(b)
    b_var = pstdev(b) ** 2
    if b_var == 0:
        return 0.0, 0.0
    cov = sum((x - fmean(r)) * (y - b_mean) for x, y in zip(r, b)) / n
    beta = cov / b_var
    r_excess = fmean(r) - risk_free
    b_excess = b_mean - risk_free
    alpha = (r_excess - beta * b_excess) * periods
    return alpha, beta


def ulcer_index(equity: list[float]) -> float:
    """Ulcer index — square root of mean squared drawdown depth."""
    dd = drawdown_series(equity)
    if not dd:
        return 0.0
    return math.sqrt(fmean(d * d for d in dd))


def omega_ratio(returns: list[float], threshold: float = 0.0) -> float:
    """Omega ratio — probability-weighted gain/loss ratio vs a threshold."""
    if not returns:
        return 0.0
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = sum(threshold - r for r in returns if r < threshold)
    if losses <= 0:
        return float("inf") if gains > 0 else 1.0
    return gains / losses


def gain_loss_ratio(returns: list[float]) -> float:
    """Mean gain over mean loss."""
    gains = [r for r in returns if r > 0]
    losses = [abs(r) for r in returns if r < 0]
    if not gains or not losses:
        return 0.0
    return fmean(gains) / fmean(losses)


def sqn(returns: list[float]) -> float:
    """System Quality Number — mean / std * sqrt(n)."""
    if len(returns) < 2:
        return 0.0
    sd = pstdev(returns)
    if sd == 0:
        return 0.0
    return fmean(returns) / sd * math.sqrt(len(returns))


def expectancy(returns: list[float]) -> float:
    """Expected return per period (mean return)."""
    return fmean(returns) if returns else 0.0


def holding_period_distribution(trades: list[Trade], bins: int = 5) -> list[int]:
    """Histogram of holding periods across trades.

    Args:
        trades: Closed trades.
        bins: Number of histogram bins.

    Returns:
        Bin counts.
    """
    if not trades:
        return []
    durations = [max(t.holding_bars, 0) for t in trades]
    counts, _ = np.histogram(durations, bins=bins)
    return [int(c) for c in counts]


def monthly_returns_table(equity: list[float], timestamps: list[Any]) -> pd.DataFrame:
    """Build a pivot table of monthly returns from an equity curve.

    Args:
        equity: Equity values.
        timestamps: Corresponding timestamps.

    Returns:
        A DataFrame with month index and return columns.
    """
    if len(equity) != len(timestamps):
        raise ValueError("equity and timestamps must be the same length.")
    if len(timestamps) == 0:
        return pd.DataFrame()
    try:
        dates = pd.to_datetime(timestamps)
        series = pd.Series(equity, index=dates)
        pct = series.pct_change().dropna()
        month = pct.index.to_period("M")
        table = pd.DataFrame({"return": pct.values, "month": month})
        pivot = table.pivot_table(
            index=month.strftime("%b").astype(str),
            columns=month.year.astype(str),
            values="return",
            aggfunc="mean",
        )
        return pivot
    except Exception as exc:
        logger.warning("Monthly returns table failed: %s", exc)
        return pd.DataFrame()


def exposure_time(equity: list[float], exposure: list[float], threshold: float = 0.0) -> float:
    """Fraction of time with exposure above *threshold* (0–1)."""
    if not exposure:
        return 0.0
    return sum(1.0 for e in exposure if e > threshold) / len(exposure)


# ═══════════════════════════════════════════════════════════════════
# PerformanceReport
# ═══════════════════════════════════════════════════════════════════


@dataclass
class AdvancedPerformanceReport:
    """Comprehensive advanced analytics report.

    Attributes:
        total_return: Total return as a decimal.
        annualized_return: Annualised return (decimal).
        volatility: Annualised volatility (decimal).
        sharpe: Annualised Sharpe.
        sortino: Annualised Sortino.
        calmar: Annualised return / max drawdown.
        max_drawdown: Maximum percentage drawdown (0–100 scale).
        recovery_factor: Total return / max drawdown depth.
        mar_ratio: Annualised return / max drawdown (percentage terms).
        ulcer_index: Ulcer index.
        omega: Omega ratio.
        gain_loss_ratio: Mean gain / mean loss.
        sqn: System Quality Number.
        expectancy: Mean period return.
        alpha: Annualised alpha vs benchmark.
        beta: Beta vs benchmark.
        trade_count: Number of closed trades.
        win_rate: Win rate percentage (0–100).
        profit_factor: Gross profit / gross loss.
        avg_trade_return: Mean per-trade return (decimal).
        avg_trade_duration: Mean holding bars.
        exposure_time: Fraction of time in the market.
        holding_distribution: Histogram of holding periods.
        equity_curve: Full equity series.
        drawdown_series: Full drawdown series.
        rolling_sharpe: Rolling Sharpe series.
        rolling_sortino: Rolling Sortino series.
        rolling_drawdown: Rolling drawdown series.
    """

    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    recovery_factor: float = 0.0
    mar_ratio: float = 0.0
    ulcer_index: float = 0.0
    omega: float = 0.0
    gain_loss_ratio: float = 0.0
    sqn: float = 0.0
    expectancy: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_trade_return: float = 0.0
    avg_trade_duration: float = 0.0
    exposure_time: float = 0.0
    holding_distribution: list[int] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    drawdown_series: list[float] = field(default_factory=list)
    rolling_sharpe: list[float | None] = field(default_factory=list)
    rolling_sortino: list[float | None] = field(default_factory=list)
    rolling_drawdown: list[float | None] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary of the headline metrics."""
        return {
            "total_return": round(self.total_return, 6),
            "annualized_return": round(self.annualized_return, 6),
            "volatility": round(self.volatility, 6),
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "calmar": round(self.calmar, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "recovery_factor": round(self.recovery_factor, 4),
            "mar_ratio": round(self.mar_ratio, 4),
            "ulcer_index": round(self.ulcer_index, 4),
            "omega": round(self.omega, 4) if self.omega != float("inf") else None,
            "gain_loss_ratio": round(self.gain_loss_ratio, 4),
            "sqn": round(self.sqn, 4),
            "expectancy": round(self.expectancy, 6),
            "alpha": round(self.alpha, 6),
            "beta": round(self.beta, 4),
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 4),
            "avg_trade_return": round(self.avg_trade_return, 6),
            "avg_trade_duration": round(self.avg_trade_duration, 2),
            "exposure_time": round(self.exposure_time, 4),
        }


class AdvancedPerformanceAnalyzer:
    """Computes advanced analytics from equity, returns, and trades.

    Usage::

        analyzer = AdvancedPerformanceAnalyzer(periods_per_year=252)
        report = analyzer.analyze(
            equity=equity_curve,
            returns=period_returns,
            trades=closed_trades,
            benchmark_returns=bench_returns,
            exposure=exposure_series,
        )
    """

    def __init__(
        self,
        periods_per_year: int = 252,
        risk_free_rate: float = 0.0,
        rolling_window: int = 30,
    ) -> None:
        """Initialise the analyzer.

        Args:
            periods_per_year: Periods used for annualisation.
            risk_free_rate: Annualised risk-free rate.
            rolling_window: Window length for rolling metrics.
        """
        self.periods = max(1, int(periods_per_year))
        self.risk_free = max(0.0, float(risk_free_rate))
        self.rolling_window = max(2, int(rolling_window))

    def analyze(
        self,
        equity: list[float],
        returns: list[float],
        trades: list[Trade] | None = None,
        benchmark_returns: list[float] | None = None,
        exposure: list[float] | None = None,
    ) -> AdvancedPerformanceReport:
        """Compute the full advanced analytics report.

        Args:
            equity: Equity curve values (at least two points).
            returns: Per-period returns aligned with equity.
            trades: Closed trades for trade-level statistics.
            benchmark_returns: Optional benchmark returns for alpha/beta.
            exposure: Optional exposure series for exposure time.

        Returns:
            An :class:`AdvancedPerformanceReport`.
        """
        trades = trades or []
        equity = [float(v) for v in equity] or [0.0]
        returns = [float(v) for v in returns] or []

        if len(equity) >= 2 and equity[0] > 0:
            total_return = equity[-1] / equity[0] - 1.0
            ann_return = cagr(equity[0], equity[-1], len(equity) - 1, self.periods)
        else:
            total_return = 0.0
            ann_return = 0.0

        vol = annualized_volatility(returns, self.periods)
        rf_period = self.risk_free / self.periods
        sharpe = sharpe_ratio(returns, self.periods, rf_period)
        sortino = sortino_ratio(returns, self.periods, rf_period)

        max_dd = max_drawdown(equity)
        calmar = ann_return / (max_dd / 100.0) if max_dd > 0 else 0.0
        mar_ratio = (ann_return * 100.0) / max_dd if max_dd > 0 else 0.0

        recovery = (total_return * equity[0]) / (max_dd / 100.0 * equity[0]) if max_dd > 0 else 0.0
        ui = ulcer_index(equity)

        omega = omega_ratio(returns)
        glr = gain_loss_ratio(returns)
        sqn_val = sqn(returns)
        exp = expectancy(returns)

        alpha, beta = 0.0, 0.0
        if benchmark_returns:
            alpha, beta = alpha_beta(returns, benchmark_returns, self.periods, rf_period)

        trade_returns = [t.return_pct / 100.0 for t in trades]
        wins = sum(1 for r in trade_returns if r > 0)
        losses = sum(1 for r in trade_returns if r < 0)
        win_rate = wins / len(trade_returns) * 100.0 if trade_returns else 0.0

        gross_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
        gross_loss = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        avg_trade_return = fmean(trade_returns) if trade_returns else 0.0
        avg_duration = fmean([float(t.holding_bars) for t in trades]) if trades else 0.0

        exp_time = exposure_time(equity, exposure) if exposure else 0.0

        return AdvancedPerformanceReport(
            total_return=total_return,
            annualized_return=ann_return,
            volatility=vol,
            sharpe=sharpe,
            sortino=sortino,
            calmar=calmar,
            max_drawdown=max_dd,
            recovery_factor=recovery,
            mar_ratio=mar_ratio,
            ulcer_index=ui,
            omega=omega,
            gain_loss_ratio=glr,
            sqn=sqn_val,
            expectancy=exp,
            alpha=alpha,
            beta=beta,
            trade_count=len(trades),
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_return=avg_trade_return,
            avg_trade_duration=avg_duration,
            exposure_time=exp_time,
            holding_distribution=holding_period_distribution(trades),
            equity_curve=equity,
            drawdown_series=drawdown_series(equity),
            rolling_sharpe=rolling_sharpe(returns, self.rolling_window, self.periods),
            rolling_sortino=rolling_sortino(returns, self.rolling_window, self.periods),
            rolling_drawdown=rolling_drawdown(equity, self.rolling_window),
        )
