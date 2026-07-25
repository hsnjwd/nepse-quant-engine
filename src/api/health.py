"""Health check API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from src.logging.logger import logger

router = APIRouter(tags=["Health"])


@router.get("/", status_code=status.HTTP_200_OK)
def home() -> dict[str, str]:
    """Retrieve system health status.

    Returns:
        A dictionary containing operational status of the engine.
    """
    logger.debug("Health check ping requested.")
    return {
        "status": "NEPSE Quant Engine Running",
    }