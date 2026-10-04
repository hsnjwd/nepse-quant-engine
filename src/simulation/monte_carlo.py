"""Monte Carlo Simulation Engine for the NEPSE Quant Engine.

Evaluates the robustness of a trading strategy by repeatedly simulating
thousands of possible trade sequences using bootstrap resampling, order
shuffling, and return compounding.

Integrates with the existing :mod:`~src.backtest.metrics` and
:mod:`~src.analytics.performance` modules without modifying their APIs.
"""

from __future__ import annotations

import copy
import csv
import math
import random
from dataclasses import dataclass, field
from statistics import fmean, median, pstdev
from typing import Any

from src.logging.logger import logger


# ======================================================================
# Constants
# ======================================================================

_MIN_TRADES = 1
_DEFAULT_SIMULATIONS = 10_000
_DEFAULT_CONFIDENCE = 0.95
_RUIN_DRAWDOWN_THRESHOLD = 50.0  # percentage

_FIVE_PCT = 0.05
_TEN_PCT = 0.10


# ======================================================================
# SimulationResult — per-simulation output dataclass
# ======================================================================


@dataclass
class SimulationResult:
    """Result of a single Monte Carlo simulation run.

    Attributes:
        simulation_number: Ordinal index of the simulation (1-based).
        ending_equity: Final cumulative net profit (equity value) at the
            end of the simulation.
        net_profit: Sum of all net profits across the simulated trades.
        max_drawdown: Maximum peak-to-trough decline of the equity curve
            as a positive percentage.
        return_pct: Percentage return of the equity curve (computed from
            peak equity, or ``0.0`` when the curve never goes positive).
        win_rate: Percentage of simulated trades that had a positive
            net profit.
        profit_factor: Ratio of gross profit to absolute gross loss
            across the simulated trades.
    """

    simulation_number: int
    ending_equity: float
    net_profit: float
    max_drawdown: float
    return_pct: float
    win_rate: float
    profit_factor: float

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with all simulation result fields.
        """
        return {
            "simulation_number": self.simulation_number,
            "ending_equity": round(self.ending_equity, 2),
            "net_profit": round(self.net_profit, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "return_pct": round(self.return_pct, 2),
            "win_rate": round(self.win_rate, 2),
            "profit_factor": (
                self.profit_factor
                if self.profit_factor is not None
                else None
            ),
        }


# ======================================================================
# MonteCarloSummary — aggregate output dataclass
# ======================================================================


@dataclass
class MonteCarloSummary:
    """Aggregate summary of all Monte Carlo simulation runs.

    Attributes:
        simulations:
            Total number of simulations run.
        mean_return:
            Mean ending equity across all simulations.
        median_return:
            Median ending equity across all simulations.
        best_return:
            Highest ending equity.
        worst_return:
            Lowest ending equity.
        mean_drawdown:
            Mean maximum drawdown across all simulations (percentage).
        max_drawdown:
            Highest maximum drawdown across all simulations (percentage).
        probability_of_profit:
            Percentage of simulations that ended with positive equity.
        probability_of_loss:
            Percentage of simulations that ended with negative equity.
        probability_of_ruin:
            Percentage of simulations whose equity curve experienced a
            drawdown exceeding 50 % of its peak.
        value_at_risk_95:
            5th-percentile ending equity (Value at Risk at 95 %
            confidence).
        conditional_var_95:
            Expected shortfall — mean ending equity of the worst 5 % of
            simulations.
        best_equity:
            Equity curve (list of cumulative profit values) corresponding
            to the simulation with the highest ending equity.
        worst_equity:
            Equity curve corresponding to the simulation with the lowest
            ending equity.
        average_equity:
            Pointwise average equity curve across all simulations.
        confidence_interval:
            Dictionary with ``lower`` and ``upper`` bounds for ending
            equity at the configured confidence level.
        equity_curves:
            Full list of equity curves from every simulation.
            Each curve is a list of cumulative profit values starting
            from ``0.0``.
        percentiles:
            Dictionary with ``p5``, ``p10``, ``p25``, ``p50``, ``p75``,
            ``p90``, ``p95``, and ``p99`` ending-equity percentiles.
    """

    simulations: int

    mean_return: float = 0.0
    median_return: float = 0.0
    best_return: float = 0.0
    worst_return: float = 0.0

    mean_drawdown: float = 0.0
    max_drawdown: float = 0.0

    probability_of_profit: float = 0.0
    probability_of_loss: float = 0.0
    probability_of_ruin: float = 0.0

    value_at_risk_95: float = 0.0
    conditional_var_95: float = 0.0

    best_equity: list[float] = field(default_factory=list)
    worst_equity: list[float] = field(default_factory=list)
    average_equity: list[float] = field(default_factory=list)

    confidence_interval: dict[str, float] = field(default_factory=dict)

    equity_curves: list[list[float]] = field(default_factory=list)

    percentiles: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with all summary fields. Equity curves are
            included as nested lists.
        """
        return {
            "simulations": self.simulations,
            "mean_return": round(self.mean_return, 2),
            "median_return": round(self.median_return, 2),
            "best_return": round(self.best_return, 2),
            "worst_return": round(self.worst_return, 2),
            "mean_drawdown": round(self.mean_drawdown, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "probability_of_profit": round(self.probability_of_profit, 2),
            "probability_of_loss": round(self.probability_of_loss, 2),
            "probability_of_ruin": round(self.probability_of_ruin, 2),
            "value_at_risk_95": round(self.value_at_risk_95, 2),
            "conditional_var_95": round(self.conditional_var_95, 2),
            "confidence_interval": {
                k: round(v, 2) for k, v in self.confidence_interval.items()
            },
            "percentiles": {
                k: round(v, 2) for k, v in self.percentiles.items()
            },
        }


# ======================================================================
# MonteCarloSimulator
# ======================================================================


class MonteCarloSimulator:
    """Monte Carlo simulation engine for trading strategy evaluation.

    Repeatedly simulates thousands of possible trade sequences using
    three complementary methods:

    * **Bootstrap** — random sampling with replacement from the original
      trade list (``simulate_bootstrap``).
    * **Shuffle** — random permutation of trade order (``simulate_shuffle``).
    * **Returns** — compounding sampled percentage returns to generate
      equity curves (``simulate_returns``).

    After running a simulation method, call :meth:`summary` to obtain an
    aggregate :class:`MonteCarloSummary` with risk metrics and
    percentiles.

    Usage::

        from src.simulation.monte_carlo import MonteCarloSimulator

        simulator = MonteCarloSimulator(simulations=5_000, random_seed=42)
        results = simulator.simulate_bootstrap(trades)
        summary = simulator.summary(results)
        print(summary.to_dict())
    """

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        simulations: int = _DEFAULT_SIMULATIONS,
        confidence_level: float = _DEFAULT_CONFIDENCE,
        random_seed: int | None = None,
    ) -> None:
        """Initialise the Monte Carlo simulator.

        Args:
            simulations:
                Number of simulation runs to execute.  Must be a
                positive integer.
            confidence_level:
                Confidence level for interval estimation.  Must be
                between ``0.0`` and ``1.0``.
            random_seed:
                Optional seed for the random number generator, enabling
                reproducible simulations.

        Raises:
            ValueError: If ``simulations`` is not positive or
                ``confidence_level`` is not in ``(0, 1]``.
        """
        if simulations <= 0:
            raise ValueError(
                f"simulations must be positive, got {simulations}."
            )
        if not 0 < confidence_level <= 1:
            raise ValueError(
                f"confidence_level must be in (0, 1], got {confidence_level}."
            )

        self._simulations = simulations
        self._confidence_level = confidence_level
        self._rng = random.Random(random_seed)

        logger.info(
            "MonteCarloSimulator initialised: simulations=%d, "
            "confidence=%.2f, seed=%s.",
            simulations,
            confidence_level,
            str(random_seed),
        )

    # ------------------------------------------------------------------
    # Public simulation methods
    # ------------------------------------------------------------------

    def simulate_bootstrap(
        self,
        trades: list[dict[str, Any]] | list[Any],
    ) -> list[SimulationResult]:
        """Simulate trade sequences by resampling WITH replacement.

        Each simulation randomly draws ``len(trades)`` trades from the
        original list (allowing repeats) and computes performance
        metrics on the resulting sequence.

        Args:
            trades: List of trade dicts or objects.  Each element must
                support dict-like access to ``net_profit``,
                ``return_pct``, and ``holding_days`` keys.

        Returns:
            List of :class:`SimulationResult` instances, one per
            simulation run.

        Raises:
            ValueError: If ``trades`` is empty.
        """
        return self._run_resample(trades, replace=True)

    def simulate_shuffle(
        self,
        trades: list[dict[str, Any]] | list[Any],
    ) -> list[SimulationResult]:
        """Simulate trade sequences by randomly shuffling order.

        Each simulation is a random permutation of the original trade
        list (no replacement — every original trade appears exactly once
        per simulation).

        Args:
            trades: List of trade dicts or objects.  Each element must
                support dict-like access to ``net_profit``,
                ``return_pct``, and ``holding_days`` keys.

        Returns:
            List of :class:`SimulationResult` instances, one per
            simulation run.

        Raises:
            ValueError: If ``trades`` is empty.
        """
        return self._run_resample(trades, replace=False)

    def simulate_returns(
        self,
        trades: list[dict[str, Any]] | list[Any],
    ) -> list[SimulationResult]:
        """Simulate equity curves by compounding sampled return_pct values.

        Each simulation randomly samples ``len(trades)`` return
        percentages (with replacement) and compounds them to produce an
        equity curve using ``equity_{i+1} = equity_i + equity_i * r / 100``.

        Args:
            trades: List of trade dicts or objects.  Each element must
                contain a ``return_pct`` key.

        Returns:
            List of :class:`SimulationResult` instances, one per
            simulation run.

        Raises:
            ValueError: If ``trades`` is empty.
        """
        self._validate_trades(trades)

        return_pcts = [float(t.get("return_pct", 0.0)) for t in trades]
        n = len(return_pcts)

        logger.info(
            "Starting return-compounding simulation: %d runs, %d trades each.",
            self._simulations,
            n,
        )

        results: list[SimulationResult] = []
        log_interval = max(1, self._simulations // 10)

        for sim_idx in range(1, self._simulations + 1):
            sampled = [self._rng.choice(return_pcts) for _ in range(n)]

            equity_curve = self._build_return_equity(sampled)
            ending_equity = equity_curve[-1] if equity_curve else 0.0
            max_dd = self._compute_equity_drawdown(equity_curve)

            # Compute win rate / profit factor from the raw returns
            wins = sum(1 for r in sampled if r > 0)
            losses = sum(1 for r in sampled if r < 0)
            win_rate = (wins / n * 100.0) if n else 0.0

            gross_profit = sum(r for r in sampled if r > 0)
            gross_loss = abs(sum(r for r in sampled if r < 0))
            pf = gross_profit / gross_loss if gross_loss else 0.0

            results.append(
                SimulationResult(
                    simulation_number=sim_idx,
                    ending_equity=ending_equity,
                    net_profit=ending_equity,
                    max_drawdown=max_dd,
                    return_pct=ending_equity,  # equity value as proxy
                    win_rate=round(win_rate, 2),
                    profit_factor=round(pf, 4),
                )
            )

            if sim_idx % log_interval == 0:
                pct = sim_idx / self._simulations * 100
                logger.debug(
                    "Return simulation progress: %d / %d (%.0f%%).",
                    sim_idx,
                    self._simulations,
                    pct,
                )

        logger.info(
            "Return simulation completed: %d results generated.",
            len(results),
        )

        return results

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(
        self,
        results: list[SimulationResult],
    ) -> MonteCarloSummary:
        """Compute an aggregate summary across all simulation results.

        Calculates central tendency, dispersion, risk metrics (VaR,
        CVaR, probability of ruin), percentiles, and a confidence
        interval for ending equity.

        Args:
            results: List of :class:`SimulationResult` instances
                returned by one of the ``simulate_*`` methods.

        Returns:
            A :class:`MonteCarloSummary` with aggregated statistics.

        Raises:
            ValueError: If ``results`` is empty.
        """
        if not results:
            raise ValueError(
                "Cannot compute summary from an empty results list."
            )

        n = len(results)

        # ---- Extract ending equities ----
        equities = [r.ending_equity for r in results]
        sorted_equities = sorted(equities)

        # ---- Central tendency ----
        mean_return = fmean(equities)
        median_return = median(equities)
        best_return = max(equities)
        worst_return = min(equities)

        # ---- Drawdown ----
        drawdowns = [r.max_drawdown for r in results]
        mean_drawdown = fmean(drawdowns) if drawdowns else 0.0
        max_drawdown = max(drawdowns) if drawdowns else 0.0

        # ---- Probabilities ----
        profitable = sum(1 for e in equities if e > 0)
        losing = sum(1 for e in equities if e < 0)
        prob_profit = (profitable / n * 100.0) if n else 0.0
        prob_loss = (losing / n * 100.0) if n else 0.0

        ruined = sum(
            1 for r in results if r.max_drawdown > _RUIN_DRAWDOWN_THRESHOLD
        )
        prob_ruin = (ruined / n * 100.0) if n else 0.0

        # ---- VaR and CVaR at 95 % ----
        var_idx = max(0, int(n * _FIVE_PCT) - 1)
        value_at_risk_95 = sorted_equities[var_idx] if sorted_equities else 0.0

        cvar_tail = sorted_equities[: var_idx + 1]
        conditional_var_95 = fmean(cvar_tail) if cvar_tail else 0.0

        # ---- Confidence interval ----
        lower_idx = max(0, int(n * (1 - self._confidence_level) / 2))
        upper_idx = min(n - 1, int(n * (1 + self._confidence_level) / 2) - 1)
        ci = {
            "lower": sorted_equities[lower_idx] if sorted_equities else 0.0,
            "upper": sorted_equities[upper_idx] if sorted_equities else 0.0,
        }

        # ---- Percentiles ----
        pcts = {
            "p5": self._percentile(sorted_equities, 5),
            "p10": self._percentile(sorted_equities, 10),
            "p25": self._percentile(sorted_equities, 25),
            "p50": self._percentile(sorted_equities, 50),
            "p75": self._percentile(sorted_equities, 75),
            "p90": self._percentile(sorted_equities, 90),
            "p95": self._percentile(sorted_equities, 95),
            "p99": self._percentile(sorted_equities, 99),
        }

        # ---- Equity curves (use list of SimulationResult) ----
        equity_curves = self._build_result_equity_curves(results)
        best_curve: list[float] = []
        worst_curve: list[float] = []
        if results:
            best_idx = equities.index(best_return)
            worst_idx = equities.index(worst_return)
            if equity_curves:
                best_curve = equity_curves[best_idx] if best_idx < len(equity_curves) else []
                worst_curve = equity_curves[worst_idx] if worst_idx < len(equity_curves) else []

        average_curve = self._average_equity_curve(equity_curves) if equity_curves else []

        logger.info(
            "Monte Carlo summary: simulations=%d, mean_return=%.2f, "
            "prob_profit=%.1f%%, var95=%.2f, prob_ruin=%.1f%%.",
            n,
            mean_return,
            prob_profit,
            value_at_risk_95,
            prob_ruin,
        )

        return MonteCarloSummary(
            simulations=n,
            mean_return=mean_return,
            median_return=median_return,
            best_return=best_return,
            worst_return=worst_return,
            mean_drawdown=mean_drawdown,
            max_drawdown=max_drawdown,
            probability_of_profit=prob_profit,
            probability_of_loss=prob_loss,
            probability_of_ruin=prob_ruin,
            value_at_risk_95=value_at_risk_95,
            conditional_var_95=conditional_var_95,
            best_equity=best_curve,
            worst_equity=worst_curve,
            average_equity=average_curve,
            confidence_interval=ci,
            equity_curves=equity_curves,
            percentiles=pcts,
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_csv(
        self,
        path: str,
        results: list[SimulationResult],
    ) -> None:
        """Export simulation results to a CSV file.

        Args:
            path: File path for the CSV output.
            results: List of :class:`SimulationResult` instances to
                export.

        Raises:
            ValueError: If ``results`` is empty.
        """
        if not results:
            raise ValueError("Cannot export empty results list.")

        fieldnames = [
            "simulation_number",
            "ending_equity",
            "net_profit",
            "max_drawdown",
            "return_pct",
            "win_rate",
            "profit_factor",
        ]

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for res in results:
                writer.writerow(res.to_dict())

        logger.info("Exported %d results to '%s'.", len(results), path)

    # ------------------------------------------------------------------
    # Internal — shared resample logic
    # ------------------------------------------------------------------

    def _run_resample(
        self,
        trades: list[dict[str, Any]] | list[Any],
        replace: bool,
    ) -> list[SimulationResult]:
        """Run bootstrap (replace=True) or shuffle (replace=False).

        Args:
            trades: List of trade dicts or objects.
            replace: Whether to sample with replacement.

        Returns:
            List of :class:`SimulationResult` instances.
        """
        self._validate_trades(trades)

        n = len(trades)

        method = "bootstrap" if replace else "shuffle"
        logger.info(
            "Starting %s simulation: %d runs, %d trades each.",
            method,
            self._simulations,
            n,
        )

        results: list[SimulationResult] = []
        log_interval = max(1, self._simulations // 10)

        for sim_idx in range(1, self._simulations + 1):
            if replace:
                sampled = [self._rng.choice(trades) for _ in range(n)]
            else:
                sampled = list(trades)
                self._rng.shuffle(sampled)

            # Compute metrics on the sampled sequence
            net_profits = [float(t.get("net_profit", 0.0)) for t in sampled]

            # Equity curve (cumulative net profit starting from 0)
            equity_curve = self._build_equity_curve(net_profits)
            ending_equity = equity_curve[-1] if equity_curve else 0.0
            net_profit = sum(net_profits)
            max_dd = self._compute_equity_drawdown(equity_curve)

            # Win rate
            wins = sum(1 for v in net_profits if v > 0)
            win_rate = (wins / n * 100.0) if n else 0.0

            # Profit factor
            gross_profit = sum(v for v in net_profits if v > 0)
            gross_loss = abs(sum(v for v in net_profits if v < 0))
            pf = gross_profit / gross_loss if gross_loss else 0.0

            # Return pct from peak equity (or 0 if never positive)
            return_pct = self._equity_return_pct(equity_curve)

            results.append(
                SimulationResult(
                    simulation_number=sim_idx,
                    ending_equity=ending_equity,
                    net_profit=net_profit,
                    max_drawdown=max_dd,
                    return_pct=return_pct,
                    win_rate=round(win_rate, 2),
                    profit_factor=round(pf, 4),
                )
            )

            if sim_idx % log_interval == 0:
                pct = sim_idx / self._simulations * 100
                logger.debug(
                    "%s progress: %d / %d (%.0f%%).",
                    method.capitalize(),
                    sim_idx,
                    self._simulations,
                    pct,
                )

        logger.info(
            "%s simulation completed: %d results generated.",
            method.capitalize(),
            len(results),
        )

        return results

    # ------------------------------------------------------------------
    # Internal — helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_trades(trades: list[Any]) -> None:
        """Validate that the trade list is non-empty."""
        if not trades:
            raise ValueError(
                "Trade list is empty. Provide at least one trade."
            )

    @staticmethod
    def _build_equity_curve(net_profits: list[float]) -> list[float]:
        """Build an equity curve from a list of net profits.

        The curve starts at ``0.0`` (zero cumulative P&L) and
        accumulates each net profit sequentially.

        Args:
            net_profits: Ordered list of per-trade net profits.

        Returns:
            List of cumulative equity values (length = len + 1).
        """
        curve: list[float] = [0.0]
        for pnl in net_profits:
            curve.append(curve[-1] + pnl)
        return curve

    @staticmethod
    def _build_return_equity(return_pcts: list[float]) -> list[float]:
        """Build an equity curve by compounding percentage returns.

        Uses ``equity_{i+1} = equity_i + equity_i * r / 100``,
        starting from an initial equity of ``100.0``.

        Args:
            return_pcts: List of per-trade percentage returns.

        Returns:
            List of compounded equity values (length = len + 1).
        """
        curve: list[float] = [100.0]
        for r in return_pcts:
            curve.append(curve[-1] + curve[-1] * r / 100.0)
        return curve

    @staticmethod
    def _compute_equity_drawdown(equity_curve: list[float]) -> float:
        """Compute the maximum peak-to-trough drawdown as a percentage.

        When the entire equity curve is at or below zero (the strategy
        never generated positive cumulative profit), the drawdown is
        treated as 100% — any negative equity is a total loss from the
        starting baseline of 0.

        Args:
            equity_curve: Ordered equity values.

        Returns:
            Maximum drawdown as a positive percentage, or ``0.0``.
        """
        peak: float | None = None
        max_dd = 0.0
        all_non_positive = True

        for value in equity_curve:
            if peak is None or value > peak:
                peak = value
            if peak is not None and peak > 0 and value < peak:
                dd = (peak - value) / peak * 100.0
                if dd > max_dd:
                    max_dd = dd
            if value > 0:
                all_non_positive = False

        # If the entire curve is at or below zero, treat the drop from
        # the starting baseline as 100% drawdown.
        if all_non_positive and len(equity_curve) > 1:
            return 100.0

        return max_dd

    @staticmethod
    def _equity_return_pct(equity_curve: list[float]) -> float:
        """Compute the return percentage from peak equity.

        Args:
            equity_curve: Ordered equity values.

        Returns:
            Percentage return from the max equity to the final equity,
            or ``0.0`` when the curve never went positive.
        """
        if not equity_curve:
            return 0.0
        peak = max(equity_curve)
        final = equity_curve[-1]
        if peak <= 0:
            return 0.0
        return (final - peak) / peak * 100.0

    @staticmethod
    def _percentile(
        sorted_data: list[float],
        percentile: float,
    ) -> float:
        """Compute a percentile from a sorted list (linear interpolation).

        Args:
            sorted_data: Ascending-sorted list of values.
            percentile: Desired percentile (0–100).

        Returns:
            The interpolated value at the given percentile, or ``0.0``
            when data is empty.
        """
        if not sorted_data:
            return 0.0
        if len(sorted_data) == 1:
            return sorted_data[0]

        k = (percentile / 100.0) * (len(sorted_data) - 1)
        f = math.floor(k)
        c = math.ceil(k)

        if f == c:
            return sorted_data[int(k)]

        d0 = sorted_data[int(f)] * (c - k)
        d1 = sorted_data[int(c)] * (k - f)
        return d0 + d1

    def _build_result_equity_curves(
        self,
        results: list[SimulationResult],
    ) -> list[list[float]]:
        """Rebuild equity curves from the simulation results.

        Since we don't store full equity curves in each
        :class:`SimulationResult`, we reconstruct them using the
        ``net_profit`` values.  This is a simplified approximation that
        assumes a single net_profit per simulation.  For detailed
        per-trade equity curves, the caller should build them during
        simulation.

        Returns:
            List of equity curves (each a list of two points:
            ``[0.0, ending_equity]``).
        """
        # Simplified: each simulation's equity curve is [0, ending_equity]
        # A real implementation would store per-trade equity curves.
        curves: list[list[float]] = []
        for res in results:
            curves.append([0.0, res.ending_equity])
        return curves

    @staticmethod
    def _average_equity_curve(
        curves: list[list[float]],
    ) -> list[float]:
        """Compute the pointwise average of multiple equity curves.

        All curves must have the same length.

        Args:
            curves: List of equity curve lists (all same length).

        Returns:
            Pointwise average curve, or an empty list if ``curves`` is
            empty.
        """
        if not curves:
            return []
        if not curves[0]:
            return []

        length = len(curves[0])
        avg: list[float] = []
        for i in range(length):
            values = [c[i] for c in curves if i < len(c)]
            avg.append(fmean(values) if values else 0.0)
        return avg
