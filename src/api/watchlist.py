"""Watchlist management and scanning API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.logging.logger import logger
from src.watchlist.manager import (
    add_stock,
    load_watchlist,
    remove_stock,
)
from src.watchlist.scanner import scan_watchlist

router = APIRouter(
    prefix="/watchlist",
    tags=["Watchlist"],
)


@router.get("", status_code=status.HTTP_200_OK)
@router.get("/", status_code=status.HTTP_200_OK, include_in_schema=False)
def get_watchlist() -> Any:
    """Retrieve the current user watchlist.

    Returns:
        List or dataset containing current watchlist stock symbols.

    Raises:
        HTTPException: If loading watchlist encounters an error (500).
    """
    try:
        logger.debug("Loading watchlist data.")
        return load_watchlist()
    except Exception as err:
        logger.error("Error loading watchlist: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load watchlist: {err}",
        ) from err


@router.post("/add/{symbol}", status_code=status.HTTP_200_OK)
def add(symbol: str) -> dict[str, str]:
    """Add a stock symbol to the watchlist.

    Args:
        symbol: Stock symbol or ticker identifier.

    Returns:
        Confirmation dictionary with status and upper-cased symbol.

    Raises:
        HTTPException: If symbol format is invalid (400) or addition fails (500).
    """
    clean_symbol = symbol.strip().upper()

    if not clean_symbol or "/" in clean_symbol or "\\" in clean_symbol or ".." in clean_symbol:
        logger.warning("Invalid symbol format in watchlist add: %s", symbol)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stock symbol format: '{symbol}'",
        )

    try:
        logger.info("Adding symbol %s to watchlist.", clean_symbol)
        add_stock(clean_symbol)
        return {
            "status": "added",
            "symbol": clean_symbol,
        }
    except Exception as err:
        logger.error("Error adding symbol %s to watchlist: %s", clean_symbol, err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add symbol {clean_symbol} to watchlist: {err}",
        ) from err


@router.delete("/remove/{symbol}", status_code=status.HTTP_200_OK)
def remove(symbol: str) -> dict[str, str]:
    """Remove a stock symbol from the watchlist.

    Args:
        symbol: Stock symbol or ticker identifier.

    Returns:
        Confirmation dictionary with status and upper-cased symbol.

    Raises:
        HTTPException: If symbol format is invalid (400) or removal fails (500).
    """
    clean_symbol = symbol.strip().upper()

    if not clean_symbol or "/" in clean_symbol or "\\" in clean_symbol or ".." in clean_symbol:
        logger.warning("Invalid symbol format in watchlist remove: %s", symbol)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stock symbol format: '{symbol}'",
        )

    try:
        logger.info("Removing symbol %s from watchlist.", clean_symbol)
        remove_stock(clean_symbol)
        return {
            "status": "removed",
            "symbol": clean_symbol,
        }
    except Exception as err:
        logger.error("Error removing symbol %s from watchlist: %s", clean_symbol, err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove symbol {clean_symbol} from watchlist: {err}",
        ) from err


@router.get("/scan", status_code=status.HTTP_200_OK)
def scan() -> Any:
    """Scan all stocks in the current watchlist for trading signals.

    Returns:
        Scan results dataset for symbols in the watchlist.

    Raises:
        HTTPException: If scanning encounters an error (500).
    """
    try:
        logger.info("Executing watchlist scan.")
        return scan_watchlist()
    except Exception as err:
        logger.error("Error scanning watchlist: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scan watchlist: {err}",
        ) from err