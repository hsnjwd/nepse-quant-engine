"""Stock analysis API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.config import DATA_DIRECTORY
from src.engine.analyzer import analyze_stock
from src.logging.logger import logger

router = APIRouter()


@router.get("/{symbol}", status_code=status.HTTP_200_OK)
def analyze(symbol: str) -> dict[str, Any]:
    """Analyze historical market indicators for a given stock symbol.

    Args:
        symbol: Stock symbol or ticker identifier.

    Returns:
        A dictionary containing indicator scores, signal, target, and stop loss.

    Raises:
        HTTPException: If the symbol is invalid (400), data is missing (404),
            or analysis fails (500).
    """
    clean_symbol = symbol.strip().lower()

    if not clean_symbol or "/" in clean_symbol or "\\" in clean_symbol or ".." in clean_symbol:
        logger.warning("Invalid symbol format in analyze endpoint: %s", symbol)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stock symbol format: '{symbol}'",
        )

    file_path = Path(DATA_DIRECTORY) / f"{clean_symbol}.csv"

    if not file_path.exists():
        logger.info("Stock data file not found for symbol: %s", clean_symbol)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock data not found: {clean_symbol.upper()}",
        )

    try:
        logger.info("Executing stock analysis for symbol: %s", clean_symbol.upper())
        result: dict[str, Any] = analyze_stock(str(file_path))
        result["symbol"] = clean_symbol.upper()
        return result
    except Exception as err:
        logger.error("Error analyzing stock data for %s: %s", clean_symbol.upper(), err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to analyze stock data for {clean_symbol.upper()}: {err}",
        ) from err
    