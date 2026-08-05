"""Market Replay API endpoints — replay session control."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.api.schemas import ok
from src.logging.logger import logger

router = APIRouter(prefix="/replay", tags=["Market Replay"])

_sessions: dict[str, Any] = {}


def _get_session(session_id: str) -> Any:
    """Return a replay session or raise 404."""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Replay session '{session_id}' not found.",
        )
    return session


@router.post("/start", status_code=status.HTTP_200_OK)
def start_replay(payload: dict[str, Any]) -> dict[str, Any]:
    """Start a replay session.

    Args:
        payload: Dict with ``symbol`` and ``days``.

    Returns:
        Session id and metadata.
    """
    try:
        from src.data import DataService
        from src.replay.session import ReplaySession

        symbol = payload.get("symbol", "")
        if not symbol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="symbol is required.",
            )
        history = DataService().get_history(
            symbol, days=int(payload.get("days", 120))
        )
        if history.df is None or history.df.empty:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No history available for '{symbol}'.",
            )
        session = ReplaySession(history.df)
        _sessions[session.session_id] = session
        return ok(
            {
                "session_id": session.session_id,
                "total_bars": session.total_bars,
                "symbol": symbol.upper(),
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Replay start failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/{session_id}/step", status_code=status.HTTP_200_OK)
def step(session_id: str, steps: int = 1) -> dict[str, Any]:
    """Advance a replay session.

    Args:
        session_id: Session identifier.
        steps: Number of bars to advance.

    Returns:
        Current replay state.
    """
    session = _get_session(session_id)
    session.step(steps)
    return ok(session.state())


@router.get("/{session_id}/state", status_code=status.HTTP_200_OK)
def state(session_id: str) -> dict[str, Any]:
    """Return the current replay state.

    Args:
        session_id: Session identifier.

    Returns:
        Replay state dict.
    """
    return ok(_get_session(session_id).state())


@router.post("/{session_id}/speed", status_code=status.HTTP_200_OK)
def set_speed(session_id: str, speed: float = 1.0) -> dict[str, Any]:
    """Adjust replay playback speed.

    Args:
        session_id: Session identifier.
        speed: Multiplier.

    Returns:
        Updated state.
    """
    session = _get_session(session_id)
    session.speed = max(0.1, float(speed))
    return ok(session.state())
