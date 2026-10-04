"""Risk API endpoints — RiskLab measurements and Monte Carlo."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.api.schemas import ok
from src.logging.logger import logger

router = APIRouter(prefix="/risk", tags=["Risk"])


def _build_lab(payload: dict[str, Any]) -> Any:
    """Build a RiskLab from a returns payload."""
    import pandas as pd

    from src.risk.lab import RiskLab

    returns = payload.get("returns")
    if returns is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="returns is required.",
        )
    series = pd.Series(returns).dropna().to_numpy(dtype=float)
    return RiskLab(
        series,
        portfolio_value=float(payload.get("portfolio_value", 1_000_000.0)),
        seed=int(payload.get("seed", 42)) if payload.get("seed") is not None else None,
    )


@router.post("/var", status_code=status.HTTP_200_OK)
def value_at_risk(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute Value at Risk and Expected Shortfall.

    Args:
        payload: Dict with ``returns``, optional ``confidence`` and
            ``method``.

    Returns:
        A :class:`VaRResult` dict.
    """
    try:
        lab = _build_lab(payload)
        return ok(
            lab.var(
                confidence=float(payload.get("confidence", 0.95)),
                method=payload.get("method", "historical"),
            ).to_dict()
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("VaR calculation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/monte-carlo", status_code=status.HTTP_200_OK)
def monte_carlo(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a Monte Carlo simulation.

    Args:
        payload: Dict with ``returns``, ``simulations``, ``horizon``.

    Returns:
        A :class:`MonteCarloLabResult` dict.
    """
    try:
        lab = _build_lab(payload)
        return ok(
            lab.monte_carlo(
                simulations=int(payload.get("simulations", 5_000)),
                horizon=int(payload.get("horizon", 252)),
            ).to_dict()
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Monte Carlo failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/stress-test", status_code=status.HTTP_200_OK)
def stress_test(payload: dict[str, Any]) -> dict[str, Any]:
    """Run standard stress scenarios.

    Args:
        payload: Dict with ``returns`` and ``portfolio_value``.

    Returns:
        List of :class:`StressTestResult` dicts.
    """
    try:
        lab = _build_lab(payload)
        return ok([s.to_dict() for s in lab.stress_test()])
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Stress test failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
