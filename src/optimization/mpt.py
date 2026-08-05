"""Modern Portfolio Theory (MPT) optimizer.

Computes the efficient frontier, minimum-variance portfolio, maximum
Sharpe (tangency) portfolio, and Markowitz optimal weights for a set
of assets using expected returns and a covariance matrix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("nepse.optimization.mpt")


@dataclass
class PortfolioResult:
    """Optimization result for one portfolio.

    Attributes:
        weights: Asset weight vector (sums to 1).
        expected_return: Annualized expected return.
        volatility: Annualized volatility.
        sharpe: Sharpe ratio.
        assets: Asset names (when available).
    """

    weights: np.ndarray
    expected_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    assets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "weights": {
                self.assets[i] if i < len(self.assets) else f"asset_{i}": round(float(w), 6)
                for i, w in enumerate(self.weights)
            },
            "expected_return": round(self.expected_return, 6),
            "volatility": round(self.volatility, 6),
            "sharpe": round(self.sharpe, 6),
        }


@dataclass
class EfficientFrontier:
    """Points along the efficient frontier.

    Attributes:
        returns: Expected returns of frontier portfolios.
        volatilities: Volatilities of frontier portfolios.
        weights: Weight matrix (each row is a portfolio).
    """

    returns: list[float] = field(default_factory=list)
    volatilities: list[float] = field(default_factory=list)
    weights: list[np.ndarray] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "returns": [round(r, 6) for r in self.returns],
            "volatilities": [round(v, 6) for v in self.volatilities],
        }


class MarkowitzOptimizer:
    """Compute MPT portfolios from returns data.

    Usage::

        optimizer = MarkowitzOptimizer(returns_df)
        frontier = optimizer.efficient_frontier(n_points=50)
        tangency = optimizer.max_sharpe_portfolio()
        min_var = optimizer.minimum_variance_portfolio()
    """

    def __init__(
        self,
        returns: Any,
        risk_free_rate: float = 0.0,
        annualization: int = 252,
    ) -> None:
        """Initialise the optimizer.

        Args:
            returns: DataFrame of asset returns (columns = assets).
            risk_free_rate: Annual risk-free rate.
            annualization: Periods per year for annualization.

        Raises:
            ValueError: If returns has fewer than two columns.
        """
        self._returns = returns.dropna()
        self._assets = list(self._returns.columns)
        self._risk_free_rate = float(risk_free_rate)
        self._annualization = annualization

        if len(self._assets) < 2:
            raise ValueError("MPT requires at least two assets.")

        self._mean_returns = self._returns.mean().to_numpy() * annualization
        self._cov = self._returns.cov().to_numpy() * annualization
        self._n = len(self._assets)

        logger.debug(
            "MarkowitzOptimizer initialised for %d assets.", self._n
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def assets(self) -> list[str]:
        """Return the asset names."""
        return list(self._assets)

    def minimum_variance_portfolio(self) -> PortfolioResult:
        """Return the minimum-variance portfolio.

        Returns:
            A :class:`PortfolioResult`.
        """
        inv_cov = np.linalg.pinv(self._cov)
        ones = np.ones(self._n)
        w = inv_cov @ ones / (ones.T @ inv_cov @ ones)
        return self._result(w)

    def max_sharpe_portfolio(self) -> PortfolioResult:
        """Return the maximum Sharpe (tangency) portfolio.

        Returns:
            A :class:`PortfolioResult`.
        """
        inv_cov = np.linalg.pinv(self._cov)
        excess = self._mean_returns - self._risk_free_rate
        w = inv_cov @ excess
        if np.sum(w) == 0:
            w = np.full(self._n, 1.0 / self._n)
        else:
            w = w / np.sum(w)
        return self._result(w)

    def efficient_frontier(self, n_points: int = 50) -> EfficientFrontier:
        """Compute points along the efficient frontier.

        Args:
            n_points: Number of frontier portfolios to generate.

        Returns:
            An :class:`EfficientFrontier`.
        """
        frontier = EfficientFrontier()
        min_var = self.minimum_variance_portfolio()
        max_sharpe = self.max_sharpe_portfolio()

        targets = np.linspace(
            float(min_var.expected_return),
            float(max_sharpe.expected_return),
            max(2, n_points),
        )

        inv_cov = np.linalg.pinv(self._cov)
        ones = np.ones(self._n)

        for target in targets:
            try:
                w = self._portfolio_for_return(target, inv_cov, ones)
                result = self._result(w)
                frontier.returns.append(result.expected_return)
                frontier.volatilities.append(result.volatility)
                frontier.weights.append(w)
            except np.linalg.LinAlgError:
                continue

        return frontier

    def random_portfolios(
        self,
        n_portfolios: int = 1000,
        seed: int | None = None,
    ) -> list[PortfolioResult]:
        """Generate random weight portfolios for plotting.

        Args:
            n_portfolios: Number of random portfolios.
            seed: Optional random seed.

        Returns:
            List of :class:`PortfolioResult`.
        """
        rng = np.random.default_rng(seed)
        results: list[PortfolioResult] = []
        for _ in range(n_portfolios):
            raw = rng.random(self._n)
            w = raw / raw.sum()
            results.append(self._result(w))
        return results

    def optimize(self, objective: str = "sharpe") -> PortfolioResult:
        """Convenience wrapper around the objective portfolios.

        Args:
            objective: ``"sharpe"``, ``"min_variance"``, ``"equally_weighted"``.

        Returns:
            A :class:`PortfolioResult`.
        """
        if objective == "sharpe":
            return self.max_sharpe_portfolio()
        if objective == "min_variance":
            return self.minimum_variance_portfolio()
        if objective == "equally_weighted":
            return self._result(np.full(self._n, 1.0 / self._n))
        raise ValueError(f"Unknown objective '{objective}'.")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _result(self, weights: np.ndarray) -> PortfolioResult:
        """Build a PortfolioResult for a weight vector."""
        w = np.asarray(weights, dtype=float)
        if np.sum(w) != 0:
            w = w / np.sum(w)
        ret = float(w @ self._mean_returns)
        var = float(w @ self._cov @ w)
        vol = float(np.sqrt(max(var, 0.0)))
        sharpe = (ret - self._risk_free_rate) / vol if vol > 0 else 0.0
        return PortfolioResult(
            weights=w,
            expected_return=ret,
            volatility=vol,
            sharpe=sharpe,
            assets=list(self._assets),
        )

    def _portfolio_for_return(
        self,
        target: float,
        inv_cov: np.ndarray,
        ones: np.ndarray,
    ) -> np.ndarray:
        """Solve for the minimum-variance weights at a target return."""
        mu = self._mean_returns
        a = ones @ inv_cov @ mu
        b = mu @ inv_cov @ mu
        c = ones @ inv_cov @ ones
        d = b * c - a * a

        lambda1 = (c * target - a) / d
        lambda2 = (b - a * target) / d
        return lambda1 * (inv_cov @ mu) + lambda2 * (inv_cov @ ones)
