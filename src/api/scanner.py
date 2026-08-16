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


# ═══════════════════════════════════════════════════════════════════
# Live market status (Sprint 13.0/13.3) — zero-vs-unknown semantics
# ═══════════════════════════════════════════════════════════════════
#
# ``/market/status`` (this router, prefix ``/market``) and its top-level
# alias ``/market-status`` (see ``src/api/main.py``) both delegate to
# ``market_status_response``.  The payload carries the legacy snapshot
# keys (index/change/change_pct/volume/turnover/advances/declines/
# unchanged/status/is_open/timestamp) so existing clients keep working,
# plus an *additive* ``data_quality`` block (Phase 12/17) that tells
# consumers whether the snapshot is a genuine live print or an
# unavailable/empty summary — a provider failure must never be
# presented as ``NEPSE 0.00``.


@router.get("/status", status_code=status.HTTP_200_OK)
def market_status() -> dict[str, Any]:
    """Live NEPSE market status snapshot (index, change, turnover, volume)."""
    return market_status_response()


def market_status_payload() -> dict[str, Any]:
    """Build the live market-status payload with the data_quality block.

    Uses the centralized ``DataService`` (never a duplicate provider
    path) so the API and the dashboard report the same underlying
    system state (Sprint 13.1).  The ``data_quality`` block is computed
    by the canonical validator — an unavailable summary is labelled
    ``available=false``, never silently rendered as a real zero.
    """
    from src.data import DataService
    from src.data.quality import assess_market_summary

    svc = DataService()
    summary = svc.get_market_summary()

    provider = getattr(svc, "provider", None)
    # str() coercion: under a patched/Mock DataService (tests) attribute
    # access can auto-create a Mock; the payload must always embed a
    # plain string, never an arbitrary object (Sprint 13.3 Phase 17).
    source = str(getattr(provider, "last_provider", "") or "")

    dq = assess_market_summary(summary, source=source)

    return {
        "index": summary.index,
        "change": summary.change,
        "change_pct": summary.change_pct,
        "volume": summary.volume,
        "turnover": summary.turnover,
        "advances": summary.advances,
        "declines": summary.declines,
        "unchanged": summary.unchanged,
        "status": summary.status,
        "is_open": summary.is_market_open,
        "timestamp": summary.timestamp.isoformat() if summary.timestamp else None,
        "data_quality": dq,
    }


def market_status_response() -> dict[str, Any]:
    """HTTP response for the live market status (sanitized 500)."""
    try:
        return market_status_payload()
    except Exception as err:
        logger.error("Error fetching market status: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market status",
        ) from err
