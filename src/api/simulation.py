"""Monte Carlo simulation API endpoints."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.backtest.engine import run_backtest as execute_backtest
from src.config import DATA_DIRECTORY
from src.logging.logger import logger
from src.simulation.monte_carlo import MonteCarloSimulator, SimulationResult

router = APIRouter(
    prefix="/simulation",
    tags=["Monte Carlo Simulation"],
)


def _generate_equity_curve(
    ending_equity: float,
    num_trades: int,
    rng: random.Random,
    include_volatility: bool = True,
) -> list[float]:
    """Generate a synthetic equity curve from 0 to ending_equity.

    Creates a random walk that starts at ``0.0`` and finishes at
    ``ending_equity``, with realistic-looking intermediate variation.

    Args:
        ending_equity: Final cumulative equity value.
        num_trades: Number of steps in the curve.
        rng: Random number generator instance.
        include_volatility: Whether to add intermediate noise.

    Returns:
        List of ``num_trades + 1`` equity values.
    """
    if num_trades < 2:
        return [0.0, ending_equity]

    if not include_volatility or abs(ending_equity) < 0.01:
        # Linear interpolation for flat/zero curves
        return [ending_equity * (i / num_trades) for i in range(num_trades + 1)]

    # Generate a random-walk bridge between 0 and ending_equity
    curve: list[float] = [0.0]
    target = ending_equity
    volatility = max(abs(target) * 0.3, 1.0)

    for i in range(1, num_trades + 1):
        remaining = num_trades - i + 1
        drift = (target - curve[-1]) / max(remaining, 1)
        noise = rng.gauss(0, volatility / math.sqrt(max(remaining, 1)))
        next_val = curve[-1] + drift + noise * 0.5
        curve.append(next_val)

    # Ensure final value lands exactly on target
    if curve:
        curve[-1] = ending_equity
    return curve


def _sample_results(
    results: list[SimulationResult],
    num_trades: int,
    max_curves: int = 100,
    seed: int | None = None,
) -> list[list[float]]:
    """Sample up to *max_curves* equity curves from simulation results.

    Uses a stratified sampling strategy: the best and worst curves are
    always included, and the remaining curves are randomly selected.

    Args:
        results: Full list of SimulationResult instances.
        num_trades: Number of trades (steps) per curve.
        max_curves: Maximum number of curves to return.
        seed: Optional RNG seed for reproducibility.

    Returns:
        List of equity curve lists.
    """
    if not results:
        return []

    rng = random.Random(seed)

    # Always include best and worst
    equities = [r.ending_equity for r in results]
    best_idx = equities.index(max(equities))
    worst_idx = equities.index(min(equities))

    selected_indices = {best_idx, worst_idx}

    # Randomly sample the rest
    remaining = [i for i in range(len(results)) if i not in selected_indices]
    needed = max_curves - len(selected_indices)

    if remaining and needed > 0:
        rng.shuffle(remaining)
        selected_indices.update(remaining[:needed])

    # Build curves for selected indices
    curves: list[list[float]] = []
    for idx in sorted(selected_indices)[:max_curves]:
        if idx < len(results):
            curve = _generate_equity_curve(
                ending_equity=results[idx].ending_equity,
                num_trades=num_trades,
                rng=rng,
            )
            curves.append(curve)

    return curves


def _generate_average_curve(curves: list[list[float]]) -> list[float]:
    """Compute the pointwise average of multiple equity curves.

    Args:
        curves: List of equity curve lists (all same length).

    Returns:
        Pointwise average, or empty list if curves is empty.
    """
    if not curves or not curves[0]:
        return []

    length = len(curves[0])
    avg: list[float] = []
    for i in range(length):
        values = [c[i] for c in curves if i < len(c)]
        avg.append(sum(values) / len(values) if values else 0.0)
    return avg


@router.get("/{symbol}", status_code=status.HTTP_200_OK)
def run_monte_carlo(
    symbol: str,
    simulations: int = Query(
        default=1_000,
        ge=10,
        le=100_000,
        description="Number of Monte Carlo simulation runs.",
    ),
    method: str = Query(
        default="bootstrap",
        description="Simulation method: bootstrap, shuffle, or returns.",
    ),
    confidence_level: float = Query(
        default=0.95,
        ge=0.5,
        le=0.999,
        description="Confidence level for interval estimation.",
    ),
    random_seed: int | None = Query(
        default=None,
        description="Optional seed for reproducible simulations.",
    ),
) -> dict[str, Any]:
    """Run a Monte Carlo simulation on backtested trades for a stock.

    First executes a historical backtest for the given symbol, then
    runs Monte Carlo simulations on the resulting trades to evaluate
    strategy robustness.

    Args:
        symbol: Stock symbol or ticker identifier.
        simulations: Number of simulation runs (10–100,000).
        method: Simulation method — ``bootstrap`` (resample with
            replacement), ``shuffle`` (permute order), or ``returns``
            (compound sampled return percentages).
        confidence_level: Confidence level for interval estimation.
        random_seed: Optional seed for reproducibility.

    Returns:
        A dictionary containing:
            - symbol: The stock symbol analysed.
            - total_trades: Total number of backtest trades.
            - simulations_run: Number of Monte Carlo runs executed.
            - method: The simulation method used.
            - summary: Aggregate :class:`MonteCarloSummary` dict.
            - equity_curves: Up to 100 sampled equity paths for
              charting.
            - best_curve: Equity curve of the best simulation.
            - worst_curve: Equity curve of the worst simulation.
            - average_curve: Pointwise average equity curve.
            - backtest_metrics: Performance metrics from the backtest.

    Raises:
        HTTPException: For invalid input (400), missing data (404),
            or execution errors (500).
    """
    clean_symbol = symbol.strip().lower()

    if not clean_symbol or "/" in clean_symbol or "\\" in clean_symbol or ".." in clean_symbol:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stock symbol format: '{symbol}'",
        )

    if method not in ("bootstrap", "shuffle", "returns"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid method '{method}'. Must be bootstrap, shuffle, or returns.",
        )

    file_path = Path(DATA_DIRECTORY) / f"{clean_symbol}.csv"
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock data not found: {clean_symbol.upper()}",
        )

    try:
        logger.info(
            "Running Monte Carlo simulation for %s: method=%s, simulations=%d.",
            clean_symbol.upper(), method, simulations,
        )

        # Step 1: Run backtest to get trades
        backtest_result = execute_backtest(str(file_path))
        trades = backtest_result.get("trades", [])
        metrics = backtest_result.get("metrics", {})

        if not trades:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No trades generated in backtest for {clean_symbol.upper()}. "
                f"Try a different stock or check data quality.",
            )

        logger.info(
            "Backtest produced %d trades for %s.",
            len(trades), clean_symbol.upper(),
        )

        # Step 2: Run Monte Carlo simulation
        simulator = MonteCarloSimulator(
            simulations=simulations,
            confidence_level=confidence_level,
            random_seed=random_seed,
        )

        if method == "bootstrap":
            results = simulator.simulate_bootstrap(trades)
        elif method == "shuffle":
            results = simulator.simulate_shuffle(trades)
        else:  # returns
            results = simulator.simulate_returns(trades)

        summary = simulator.summary(results)

        # Step 3: Generate realistic equity curves for charting
        num_trades = len(trades)
        equity_curves = _sample_results(
            results=results,
            num_trades=num_trades,
            max_curves=100,
            seed=random_seed,
        )

        # Generate best/worst/average curves
        equities = [r.ending_equity for r in results]
        best_idx = equities.index(max(equities)) if equities else 0
        worst_idx = equities.index(min(equities)) if equities else 0

        best_curve = _generate_equity_curve(
            ending_equity=results[best_idx].ending_equity if results else 0,
            num_trades=num_trades,
            rng=random.Random(random_seed),
        ) if results else []

        worst_curve = _generate_equity_curve(
            ending_equity=results[worst_idx].ending_equity if results else 0,
            num_trades=num_trades,
            rng=random.Random(random_seed),
        ) if results else []

        average_curve = _generate_average_curve(equity_curves) if equity_curves else []

        logger.info(
            "Monte Carlo simulation completed for %s: "
            "mean_return=%.2f, prob_profit=%.1f%%, var95=%.2f.",
            clean_symbol.upper(),
            summary.mean_return,
            summary.probability_of_profit,
            summary.value_at_risk_95,
        )

        return {
            "symbol": clean_symbol.upper(),
            "total_trades": len(trades),
            "simulations_run": simulations,
            "method": method,
            "confidence_level": confidence_level,
            "summary": summary.to_dict(),
            "equity_curves": equity_curves,
            "best_curve": best_curve,
            "worst_curve": worst_curve,
            "average_curve": average_curve,
            "backtest_metrics": metrics,
        }

    except HTTPException:
        raise
    except Exception as err:
        logger.error(
            "Error running Monte Carlo for %s: %s",
            clean_symbol, err,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Monte Carlo simulation failed: {err}",
        ) from err
