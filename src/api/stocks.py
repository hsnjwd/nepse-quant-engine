"""Stocks API endpoints — live quotes and history via DataService."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.api.schemas import mover_to_dict, ok, paginate, quote_to_dict
from src.logging.logger import logger

router = APIRouter(prefix="/stocks", tags=["Stocks"])


@router.get("/", status_code=status.HTTP_200_OK)
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List all stocks from the live market.

    Args:
        page: Page number.
        page_size: Items per page.

    Returns:
        A paginated list of quotes.
    """
    try:
        from src.data import DataService

        quotes = DataService().get_live_market()
        quotes_data = [quote_to_dict(q) for q in quotes] if quotes else []
        return ok(paginate(quotes_data, page, page_size).to_dict())
    except Exception as exc:
        logger.error("Failed to list stocks: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live market unavailable.",
        ) from exc


@router.get("/{symbol}", status_code=status.HTTP_200_OK)
def get_stock(symbol: str) -> dict[str, Any]:
    """Return a single stock quote and analysis.

    Args:
        symbol: Stock symbol (case-insensitive).

    Returns:
        Quote + analysis payload.
    """
    try:
        from src.data import DataService

        svc = DataService()
        quote = svc.get_stock(symbol)
        if quote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stock '{symbol}' not found.",
            )
        return ok(quote_to_dict(quote))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch stock %s: %s", symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live market unavailable.",
        ) from exc


@router.get("/{symbol}/history", status_code=status.HTTP_200_OK)
def get_stock_history(
    symbol: str,
    days: int = Query(120, ge=30, le=1000),
) -> dict[str, Any]:
    """Return historical OHLCV data for a symbol.

    Args:
        symbol: Stock symbol.
        days: Number of trading days of history.

    Returns:
        History payload with symbol, days and records.
    """
    try:
        from src.data import DataService

        history = DataService().get_history(symbol, days=days)
        df = history.df
        records = (
            df.reset_index().to_dict(orient="records")
            if df is not None and not df.empty
            else []
        )
        return ok(
            {
                "symbol": symbol.upper(),
                "days": days,
                "records": records,
                "count": len(records),
            }
        )
    except Exception as exc:
        logger.error("Failed to fetch history for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="History unavailable.",
        ) from exc
