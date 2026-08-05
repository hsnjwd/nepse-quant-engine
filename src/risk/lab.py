"""Risk Lab — Monte Carlo and risk measurement toolkit.

Extends the risk subsystem with Value-at-Risk (VaR), Conditional VaR
(Expected Shortfall), portfolio stress testing, probability of ruin,
drawdown probability, and recovery analysis, all driven by Monte Carlo
simulation over a returns history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("nepse.risk.lab")


@dataclass
class VaRResult:
    """Value-at-Risk measurement.

    Attributes:
        method: Historical / parametric / monte_carlo.
        confidence: Confidence level (0–1).
        var: Value at Risk (loss amount, positive).
        cvar: Conditional VaR / Expected Shortfall (positive).
        std: Standard deviation of returns (parametric).
        notes: Method notes.
    """

    method: str = "historical"
    confidence: float = 0.95
    var: float = 0.0
    cvar: float = 0.0
    std: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "method": self.method,
            "confidence": self.confidence,
            "var": round(self.var, 6),
            "cvar": round(self.cvar, 6),
            "std": round(self.std, 6),
            "notes": self.notes,
        }


@dataclass
class StressTestResult:
    """Outcome of a stress scenario.

    Attributes:
        scenario: Scenario name.
        shock_pct: Applied return shock (e.g. -0.10).
        portfolio_value: Starting portfolio value.
        end_value: Value after the shock.
        loss_pct: Percentage loss.
    """

    scenario: str = ""
    shock_pct: float = 0.0
    portfolio_value: float = 0.0
    end_value: float = 0.0
    loss_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "scenario": self.scenario,
            "shock_pct": self.shock_pct,
            "portfolio_value": self.portfolio_value,
            "end_value": round(self.end_value, 2),
            "loss_pct": round(self.loss_pct, 4),
        }


@dataclass
class MonteCarloLabResult:
    """Aggregate Monte Carlo risk lab output.

    Attributes:
        simulations: Number of simulations run.
        horizon: Simulation horizon in days.
        ending_values: Array of simulated ending portfolio values.
        mean_ending: Mean ending value.
        median_ending: Median ending value.
        p5 / p95: Percentile ending values.
        probability_of_loss: Fraction of simulations ending below start.
        probability_of_ruin: Fraction breaching the ruin threshold.
        max_drawdown_pct: Worst simulated drawdown.
        var_95: Historical VaR at 95%.
        cvar_95: Conditional VaR at 95%.
    """

    simulations: int = 0
    horizon: int = 252
    ending_values: np.ndarray = field(default_factory=lambda: np.array([]))
    mean_ending: float = 0.0
    median_ending: float = 0.0
    p5: float = 0.0
    p95: float = 0.0
    probability_of_loss: float = 0.0
    probability_of_ruin: float = 0.0
    max_drawdown_pct: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "simulations": self.simulations,
            "horizon": self.horizon,
            "mean_ending": round(float(self.mean_ending), 2),
            "median_ending": round(float(self.median_ending), 2),
            "p5": round(float(self.p5), 2),
            "p95": round(float(self.p95), 2),
            "probability_of_loss": round(float(self.probability_of_loss), 4),
            "probability_of_ruin": round(float(self.probability_of_ruin), 4),
            "max_drawdown_pct": round(float(self.max_drawdown_pct), 4),
            "var_95": round(float(self.var_95), 4),
            "cvar_95": round(float(self.cvar_95), 4),
        }


class RiskLab:
    """Monte Carlo driven risk measurement.

    Usage::

        lab = RiskLab(returns=df["return"])
        var = lab.var(confidence=0.95, method="historical")
        sim = lab.monte_carlo(simulations=10_000, horizon=252)
    """

    def __init__(
        self,
        returns: Any,
        portfolio_value: float = 1_000_000.0,
        seed: int | None = 42,
    ) -> None:
        """Initialise the lab.

        Args:
            returns: Series/array of periodic returns (decimal).
            portfolio_value: Current portfolio value.
            seed: Random seed for reproducibility.
        """
        self._returns = np.asarray(returns, dtype=float)
        self._returns = self._returns[~np.isnan(self._returns)]
        self._portfolio_value = float(portfolio_value)
        self._rng = np.random.default_rng(seed)

        if len(self._returns) < 20:
            logger.warning(
                "Only %d return observations provided; risk estimates "
                "will be unstable.",
                len(self._returns),
            )

    # ------------------------------------------------------------------
    # VaR / CVaR
    # ------------------------------------------------------------------

    def var(
        self,
        confidence: float = 0.95,
        method: str = "historical",
    ) -> VaRResult:
        """Compute Value at Risk.

        Args:
            confidence: Confidence level (0–1).
            method: ``"historical"``, ``"parametric"`` or
                ``"monte_carlo"``.

        Returns:
            A :class:`VaRResult`.
        """
        if not 0 < confidence < 1:
            raise ValueError("confidence must be between 0 and 1.")

        if method == "parametric":
            mean = float(np.mean(self._returns))
            std = float(np.std(self._returns))
            from scipy import stats  # type: ignore

            var = float(std * stats.norm.ppf(confidence) + mean)
            cvar = float(
                -std
                * stats.norm.pdf(stats.norm.ppf(confidence))
                / (1 - confidence)
                + mean
            )
            return VaRResult(
                method=method,
                confidence=confidence,
                var=abs(var) if var < 0 else 0.0,
                cvar=abs(cvar) if cvar < 0 else 0.0,
                std=std,
                notes=["Parametric VaR assumes normal returns."],
            )

        if method == "monte_carlo":
            simulated = self._rng.normal(
                float(np.mean(self._returns)),
                float(np.std(self._returns)),
                size=100_000,
            )
            samples = simulated
        else:
            samples = self._returns

        sorted_returns = np.sort(samples)
        index = max(0, int((1 - confidence) * len(sorted_returns)) - 1)
        var = abs(float(sorted_returns[index]))
        tail = sorted_returns[: index + 1]
        cvar = abs(float(np.mean(tail))) if len(tail) else var

        notes = (
            ["Monte Carlo VaR sampled from fitted normal distribution."]
            if method == "monte_carlo"
            else ["Historical VaR from observed returns."]
        )
        return VaRResult(
            method=method,
            confidence=confidence,
            var=var,
            cvar=cvar,
            std=float(np.std(self._returns)),
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Stress testing
    # ------------------------------------------------------------------

    def stress_test(self) -> list[StressTestResult]:
        """Run standard stress scenarios against the portfolio.

        Returns:
            List of :class:`StressTestResult` for each scenario.
        """
        scenarios = [
            ("market_crash", -0.20),
            ("bear_market", -0.10),
            ("flash_crash", -0.05),
            ("rally", 0.10),
            ("boom", 0.20),
        ]
        results: list[StressTestResult] = []
        for name, shock in scenarios:
            end = self._portfolio_value * (1 + shock)
            results.append(
                StressTestResult(
                    scenario=name,
                    shock_pct=shock,
                    portfolio_value=self._portfolio_value,
                    end_value=end,
                    loss_pct=-shock,
                )
            )
        return results

    def custom_stress(self, shock_pct: float, scenario: str = "custom") -> StressTestResult:
        """Apply a custom shock to the portfolio.

        Args:
            shock_pct: Return shock as a decimal (e.g. -0.15).
            scenario: Scenario label.

        Returns:
            A :class:`StressTestResult`.
        """
        end = self._portfolio_value * (1 + shock_pct)
        return StressTestResult(
            scenario=scenario,
            shock_pct=shock_pct,
            portfolio_value=self._portfolio_value,
            end_value=end,
            loss_pct=-shock_pct,
        )

    # ------------------------------------------------------------------
    # Monte Carlo simulation
    # ------------------------------------------------------------------

    def monte_carlo(
        self,
        simulations: int = 10_000,
        horizon: int = 252,
        ruin_threshold_pct: float = 50.0,
    ) -> MonteCarloLabResult:
        """Simulate portfolio value paths.

        Args:
            simulations: Number of simulated paths.
            horizon: Number of steps per path.
            ruin_threshold_pct: Drawdown threshold (percentage of peak)
                that defines ruin.

        Returns:
            An :class:`MonteCarloLabResult`.
        """
        if simulations < 1:
            raise ValueError("simulations must be positive.")

        mu = float(np.mean(self._returns))
        sigma = float(np.std(self._returns))
        starting = self._portfolio_value

        # Sample returns and compound.
        sampled = self._rng.normal(mu, sigma, size=(simulations, horizon))
        factors = np.cumprod(1 + sampled, axis=1)
        paths = starting * factors

        ending = paths[:, -1]
        peak = np.maximum.accumulate(paths, axis=1)
        drawdowns = (peak - paths) / peak
        max_dd = float(np.max(drawdowns) * 100) if paths.size else 0.0

        prob_loss = float(np.mean(ending < starting))
        prob_ruin = float(
            np.mean(np.max(drawdowns, axis=1) * 100 > ruin_threshold_pct)
        )

        sorted_ending = np.sort(ending)
        p5 = float(np.percentile(sorted_ending, 5))
        p95 = float(np.percentile(sorted_ending, 95))

        var_idx = max(0, int(0.05 * len(sorted_ending)) - 1)
        var_95 = abs(float(sorted_ending[var_idx] - starting))
        cvar_95 = abs(
            float(np.mean(sorted_ending[: var_idx + 1]) - starting)
        )

        return MonteCarloLabResult(
            simulations=simulations,
            horizon=horizon,
            ending_values=ending,
            mean_ending=float(np.mean(ending)),
            median_ending=float(np.median(ending)),
            p5=p5,
            p95=p95,
            probability_of_loss=prob_loss,
            probability_of_ruin=prob_ruin,
            max_drawdown_pct=max_dd,
            var_95=var_95,
            cvar_95=cvar_95,
        )

    def drawdown_probability(
        self,
        threshold_pct: float = 20.0,
        simulations: int = 5_000,
        horizon: int = 252,
    ) -> dict[str, Any]:
        """Estimate the probability of breaching a drawdown threshold.

        Args:
            threshold_pct: Drawdown threshold percentage.
            simulations: Number of simulated paths.
            horizon: Simulation horizon.

        Returns:
            Dict with ``probability`` and ``threshold_pct``.
        """
        mu = float(np.mean(self._returns))
        sigma = float(np.std(self._returns))
        sampled = self._rng.normal(mu, sigma, size=(simulations, horizon))
        paths = self._portfolio_value * np.cumprod(1 + sampled, axis=1)
        peak = np.maximum.accumulate(paths, axis=1)
        max_dd = np.max((peak - paths) / peak * 100, axis=1)
        probability = float(np.mean(max_dd > threshold_pct))
        return {"threshold_pct": threshold_pct, "probability": probability}

    def recovery_analysis(
        self,
        simulations: int = 2_000,
        horizon: int = 252,
        target_return_pct: float = 10.0,
    ) -> dict[str, Any]:
        """Estimate recovery time after a loss.

        Args:
            simulations: Number of simulated paths.
            horizon: Simulation horizon.
            target_return_pct: Recovery target return percentage.

        Returns:
            Dict with ``median_recovery_days``, ``recovery_probability``
            and ``mean_recovery_days``.
        """
        mu = float(np.mean(self._returns))
        sigma = float(np.std(self._returns))
        sampled = self._rng.normal(mu, sigma, size=(simulations, horizon))
        paths = 100.0 * np.cumprod(1 + sampled, axis=1)
        target = 100.0 * (1 + target_return_pct / 100)

        recovery_days: list[int] = []
        for row in paths:
            hit = np.flatnonzero(row >= target)
            if hit.size:
                recovery_days.append(int(hit[0]) + 1)

        if not recovery_days:
            return {
                "median_recovery_days": None,
                "mean_recovery_days": None,
                "recovery_probability": 0.0,
                "target_return_pct": target_return_pct,
            }

        return {
            "median_recovery_days": int(np.median(recovery_days)),
            "mean_recovery_days": float(np.mean(recovery_days)),
            "recovery_probability": len(recovery_days) / simulations,
            "target_return_pct": target_return_pct,
        }
