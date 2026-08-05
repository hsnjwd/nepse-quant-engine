"""ML Models API endpoints — training, listing, and prediction."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.api.schemas import ok
from src.logging.logger import logger

router = APIRouter(prefix="/models", tags=["ML Models"])


@router.get("/", status_code=status.HTTP_200_OK)
def list_models() -> dict[str, Any]:
    """List available model names and stored models.

    Returns:
        Dict with registry names and stored model metadata.
    """
    try:
        from src.ml.models import available_models
        from src.ml.model_manager import ModelManager

        return ok(
            {
                "available": available_models(),
                "stored": ModelManager().list_models(),
            }
        )
    except Exception as exc:
        logger.error("Failed to list models: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/train", status_code=status.HTTP_200_OK)
def train_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Train a model on symbol history.

    Args:
        payload: Dict with ``symbol``, optional ``model_name``,
            ``task``, ``horizon``.

    Returns:
        A :class:`TrainingResult` dict.
    """
    try:
        from src.data import DataService
        from src.ml.training import TrainingPipeline

        symbol = payload.get("symbol", "")
        if not symbol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="symbol is required.",
            )
        history = DataService().get_history(symbol, days=365)
        if history.df is None or history.df.empty:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No history available for '{symbol}'.",
            )
        result = TrainingPipeline().run(
            history.df,
            model_name=payload.get("model_name"),
            task=payload.get("task", "classification"),
            horizon=int(payload.get("horizon", 5)),
        )
        return ok(result.to_dict())
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Model training failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/predict", status_code=status.HTTP_200_OK)
def predict(payload: dict[str, Any]) -> dict[str, Any]:
    """Predict the next signal for a symbol.

    Args:
        payload: Dict with ``symbol``, optional ``model_name``,
            ``task``, ``horizon``.

    Returns:
        A :class:`PredictionResult` dict.
    """
    try:
        from src.data import DataService
        from src.ml.prediction_engine import PredictionEngine

        symbol = payload.get("symbol", "")
        if not symbol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="symbol is required.",
            )
        history = DataService().get_history(symbol, days=365)
        if history.df is None or history.df.empty:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No history available for '{symbol}'.",
            )
        result = PredictionEngine().predict(
            history.df,
            model_name=payload.get("model_name"),
            task=payload.get("task", "classification"),
            horizon=int(payload.get("horizon", 5)),
        )
        return ok(result.to_dict())
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Prediction failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
