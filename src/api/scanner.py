"""Market scanner and market summary API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.logging.logger import logger
from src.services.market_service import (
    get_buy_list,
    get_market_summary,
    get_sell_list,
    get_strong_buy_list,
    get_top10,
)

router = APIRouter()


@router.get("/", status_code=status.HTTP_200_OK)
def market() -> dict[str, Any]:
    """Retrieve overall market summary and status.

    Returns:
        A dictionary containing market-wide statistics and summaries.

    Raises:
        HTTPException: If fetching market summary fails (500).
    """
    try:
        logger.debug("Fetching market summary.")
        return get_market_summary()
    except Exception as err:
        logger.error("Error fetching market summary: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch market summary: {err}",
        ) from err


@router.get("/top10", status_code=status.HTTP_200_OK)
def top10() -> Any:
    """Retrieve top 10 ranked stocks based on indicator scores.

    Returns:
        List or dict of top 10 stock scoring records.

    Raises:
        HTTPException: If fetching top 10 rankings fails (500).
    """
    try:
        logger.debug("Fetching top 10 market scanner rankings.")
        return get_top10()
    except Exception as err:
        logger.error("Error fetching top 10 rankings: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch top 10 rankings: {err}",
        ) from err


@router.get("/buylist", status_code=status.HTTP_200_OK)
def buylist() -> Any:
    """Retrieve list of stocks with active BUY signals.

    Returns:
        List or dict of candidates triggering buy signals.

    Raises:
        HTTPException: If fetching buy list fails (500).
    """
    try:
        logger.debug("Fetching market buy list.")
        return get_buy_list()
    except Exception as err:
        logger.error("Error fetching buy list: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch buy list: {err}",
        ) from err


@router.get("/selllist", status_code=status.HTTP_200_OK)
def selllist() -> Any:
    """Retrieve list of stocks with active SELL signals.

    Returns:
        List or dict of candidates triggering sell signals.

    Raises:
        HTTPException: If fetching sell list fails (500).
    """
    try:
        logger.debug("Fetching market sell list.")
        return get_sell_list()
    except Exception as err:
        logger.error("Error fetching sell list: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch sell list: {err}",
        ) from err


@router.get("/strongbuy", status_code=status.HTTP_200_OK)
def strongbuy() -> Any:
    """Retrieve list of high-confidence STRONG BUY stock candidates.

    Returns:
        List or dict of candidates triggering strong buy signals.

    Raises:
        HTTPException: If fetching strong buy list fails (500).
    """
    try:
        logger.debug("Fetching strong buy list.")
        return get_strong_buy_list()
    except Exception as err:
        logger.error("Error fetching strong buy list: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch strong buy list: {err}",
        ) from err