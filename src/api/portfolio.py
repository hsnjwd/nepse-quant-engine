"""Portfolio analysis API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.logging.logger import logger
from src.portfolio.analyzer import analyze_portfolio

router = APIRouter()


@router.get("/", status_code=status.HTTP_200_OK)
def portfolio() -> dict[str, Any]:
    """Analyze current portfolio holdings, valuation, and risk metrics.

    Returns:
        A dictionary summarizing portfolio performance and positioning.

    Raises:
        HTTPException: If portfolio analysis encounters an execution error (500).
    """
    try:
        logger.info("Executing portfolio analysis request.")
        result: dict[str, Any] = analyze_portfolio()
        return result
    except Exception as err:
        logger.error("Error analyzing portfolio: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to analyze portfolio: {err}",
        ) from err