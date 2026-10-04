"""Optimizer API endpoints — MPT, risk parity, Kelly, genetic."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.api.schemas import ok
from src.logging.logger import logger

router = APIRouter(prefix="/optimizer", tags=["Optimizer"])


@router.post("/mpt", status_code=status.HTTP_200_OK)
def mpt_optimize(payload: dict[str, Any]) -> dict[str, Any]:
    """Optimize a portfolio with Modern Portfolio Theory.

    Args:
        payload: Dict with ``returns`` (list of series) or ``returns_df``
            records plus optional ``objective``.

    Returns:
        MPT results (min variance, max sharpe, frontier).
    """
    try:
        import pandas as pd

        from src.optimization.mpt import MarkowitzOptimizer

        frame = _returns_frame(payload)
        optimizer = MarkowitzOptimizer(
            frame,
            risk_free_rate=float(payload.get("risk_free_rate", 0.0)),
        )
        objective = payload.get("objective", "sharpe")
        best = optimizer.optimize(objective)
        frontier = optimizer.efficient_frontier(
            n_points=int(payload.get("n_points", 30))
        )
        return ok(
            {
                "best": best.to_dict(),
                "frontier": frontier.to_dict(),
                "assets": optimizer.assets,
            }
        )
    except Exception as exc:
        logger.error("MPT optimization failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/risk-parity", status_code=status.HTTP_200_OK)
def risk_parity(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute risk-parity weights.

    Args:
        payload: Dict with ``returns`` data.

    Returns:
        Risk-parity weights and contributions.
    """
    try:
        from src.optimization.risk_parity import RiskParityOptimizer

        frame = _returns_frame(payload)
        return ok(RiskParityOptimizer(frame).optimize())
    except Exception as exc:
        logger.error("Risk parity failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/kelly", status_code=status.HTTP_200_OK)
def kelly(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute Kelly fractions from trade statistics.

    Args:
        payload: Dict with ``win_rate``, ``avg_win``, ``avg_loss`` or
            ``trades``.

    Returns:
        A :class:`KellyResult` dict.
    """
    try:
        from src.optimization.kelly import KellyCriterion

        if payload.get("trades"):
            return ok(KellyCriterion.from_trades(payload["trades"]).to_dict())
        return ok(
            KellyCriterion().calculate(
                win_rate=float(payload["win_rate"]),
                avg_win=float(payload["avg_win"]),
                avg_loss=float(payload["avg_loss"]),
            ).to_dict()
        )
    except Exception as exc:
        logger.error("Kelly calculation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/genetic", status_code=status.HTTP_200_OK)
def genetic(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a genetic optimization over a parameter space.

    Args:
        payload: Dict with ``param_space`` and ``fitness_fn`` described
            as a callable import path (module:function) plus optional GA
            settings.

    Returns:
        Best individual and history.
    """
    try:
        from src.optimization.genetic import GeneticOptimizer

        param_space = payload.get("param_space")
        if not param_space:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="param_space is required.",
            )
        fitness = _resolve_fitness(payload.get("fitness_fn"))
        optimizer = GeneticOptimizer(
            param_space=param_space,
            fitness_fn=fitness,
            population_size=int(payload.get("population_size", 20)),
            generations=int(payload.get("generations", 10)),
        )
        best, history = optimizer.run()
        return ok(
            {
                "best": best.to_dict() if best else None,
                "history": history,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Genetic optimization failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _returns_frame(payload: dict[str, Any]) -> Any:
    """Build a returns DataFrame from an API payload."""
    import pandas as pd

    if payload.get("returns_df"):
        return pd.DataFrame(payload["returns_df"])
    if payload.get("returns"):
        data = payload["returns"]
        if isinstance(data, dict):
            return pd.DataFrame(data)
        return pd.DataFrame({"returns": data})
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide 'returns' or 'returns_df'.",
    )


def _resolve_fitness(spec: Any) -> Any:
    """Resolve a fitness function from a module:function string."""
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fitness_fn is required (module:function).",
        )
    if callable(spec):
        return spec
    if isinstance(spec, str):
        module_name, _, func_name = spec.partition(":")
        import importlib

        module = importlib.import_module(module_name)
        return getattr(module, func_name)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="fitness_fn must be a callable or module:function string.",
    )
