"""Risk Parity optimizer.

Computes portfolio weights so that each asset contributes equally to
total portfolio risk, using an iterative covariance-based method.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("nepse.optimization.risk_parity")


class RiskParityOptimizer:
    """Equal-risk-contribution portfolio optimizer.

    Usage::

        optimizer = RiskParityOptimizer(returns_df)
        weights = optimizer.optimize()
    """

    def __init__(
        self,
        returns: Any,
        annualization: int = 252,
        max_iter: int = 200,
        tol: float = 1e-6,
    ) -> None:
        """Initialise the optimizer.

        Args:
            returns: DataFrame of asset returns.
            annualization: Periods per year.
            max_iter: Maximum iteration count.
            tol: Convergence tolerance.
        """
        self._returns = returns.dropna()
        self._assets = list(self._returns.columns)
        self._cov = self._returns.cov().to_numpy() * annualization
        self._n = len(self._assets)
        self._max_iter = max_iter
        self._tol = tol

    @property
    def assets(self) -> list[str]:
        """Return the asset names."""
        return list(self._assets)

    def optimize(self) -> dict[str, Any]:
        """Compute equal risk-contribution weights.

        Returns:
            Dict with ``weights``, ``risk_contributions`` and
            ``portfolio_volatility``.
        """
        if self._n == 0:
            return {
                "weights": {},
                "risk_contributions": {},
                "portfolio_volatility": 0.0,
            }

        cov = self._cov + np.eye(self._n) * 1e-8
        w = np.full(self._n, 1.0 / self._n)

        for _ in range(self._max_iter):
            marginal = cov @ w
            risk = w * marginal
            total_risk = np.sum(risk)
            if total_risk <= 0:
                break
            target = total_risk / self._n
            gradient = marginal * total_risk - risk
            step = 0.1
            w_new = w - step * gradient
            w_new = np.clip(w_new, 1e-6, None)
            w_new = w_new / np.sum(w_new)
            if np.max(np.abs(w_new - w)) < self._tol:
                w = w_new
                break
            w = w_new

        marginal = cov @ w
        risk = w * marginal
        total_risk = float(np.sum(risk))
        vol = float(np.sqrt(max(w @ cov @ w, 0.0)))

        return {
            "weights": {
                self._assets[i]: round(float(w[i]), 6)
                for i in range(self._n)
            },
            "risk_contributions": {
                self._assets[i]: round(float(risk[i]), 6)
                for i in range(self._n)
            },
            "portfolio_volatility": round(vol, 6),
            "total_risk": round(total_risk, 6),
        }
