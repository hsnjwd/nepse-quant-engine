"""Signals API endpoints — market scan results and signal explanations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.api.schemas import ok, paginate
from src.logging.logger import logger

router = APIRouter(prefix="/signals", tags=["Signals"])


@router.get("/", status_code=status.HTTP_200_OK)
def get_signals(
    signal: str | None = Query(None, description="Filter: BUY/HOLD/SELL"),
    min_confidence: float = Query(0.0, ge=0.0, le=100.0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Return the latest market scan signals.

    Args:
        signal: Optional signal filter.
        min_confidence: Minimum confidence percentage.
        page: Page number.
        page_size: Items per page.

    Returns:
        Paginated list of signal payloads.
    """
    try:
        from src.data import DataService

        scan = DataService().scan_market()
        rows = getattr(scan, "results", scan) or []
        items = []
        for row in rows:
            payload = row if isinstance(row, dict) else row.to_dict()
            sig = str(payload.get("signal", "HOLD")).upper()
            conf = float(payload.get("confidence", 0.0) or 0.0)
            if signal and sig != signal.upper():
                continue
            if conf < min_confidence:
                continue
            items.append(payload)

        return ok(paginate(items, page, page_size).to_dict())
    except Exception as exc:
        logger.error("Failed to fetch signals: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live signals unavailable.",
        ) from exc


@router.post("/explain", status_code=status.HTTP_200_OK)
def explain_signal(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate a natural-language explanation for a signal.

    Args:
        payload: Dict with ``symbol`` and optionally ``analysis``,
            ``signal``, ``confidence``, ``regime``.

    Returns:
        A :class:`SignalExplanation` dict.
    """
    try:
        from src.ai.advisor import SignalAdvisor

        explanation = SignalAdvisor().explain(
            symbol=payload.get("symbol", "UNKNOWN"),
            analysis=payload.get("analysis"),
            regime=payload.get("regime"),
            signal=payload.get("signal"),
            confidence=payload.get("confidence"),
        )
        return ok(explanation.to_dict())
    except Exception as exc:
        logger.error("Failed to explain signal: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to explain signal: {exc}",
        ) from exc
