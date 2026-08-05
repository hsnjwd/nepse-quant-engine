"""Historical backtesting API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.backtest.engine import run_backtest as execute_backtest
from src.loaders.csv_loader import resolve_stock_csv_path
from src.logging.logger import logger

router = APIRouter()


@router.get("/{symbol}", status_code=status.HTTP_200_OK)
def run_backtest(
    symbol: str,
    commission: float = Query(default=0.0, ge=0.0, description="Per-side commission rate fraction."),
    slippage: float = Query(default=0.0, ge=0.0, description="Per-side adverse slippage rate fraction."),
) -> dict[str, Any]:
    """Execute a historical backtest run for a given stock symbol.

    Args:
        symbol: Stock symbol or ticker identifier.
        commission: Per-side commission rate as a decimal fraction.
        slippage: Per-side adverse slippage rate as a decimal fraction.

    Returns:
        A dictionary containing trades, performance metrics, and summary report.

    Raises:
        HTTPException: If symbol or parameters are invalid (400), data is missing (404),
            or backtest fails (500).
    """
    clean_symbol = symbol.strip().lower()

    if not clean_symbol or "/" in clean_symbol or "\\" in clean_symbol or ".." in clean_symbol:
        logger.warning("Invalid symbol format in backtest endpoint: %s", symbol)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stock symbol format: '{symbol}'",
        )

    if commission < 0 or slippage < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Commission and slippage rates must be non-negative.",
        )

    file_path = resolve_stock_csv_path(clean_symbol)

    if file_path is None:
        logger.info("Stock data file not found for backtest: %s", clean_symbol)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock data not found: {clean_symbol.upper()}",
        )

    try:
        logger.info("Executing backtest for symbol: %s", clean_symbol.upper())
        return execute_backtest(str(file_path), commission=commission, slippage=slippage)
    except Exception as err:
        logger.error("Error executing backtest for %s: %s", clean_symbol.upper(), err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute backtest for {clean_symbol.upper()}: {err}",
        ) from err