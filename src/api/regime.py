"""Market regime detection API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.config import DATA_DIRECTORY
from src.loaders.csv_loader import load_csv
from src.logging.logger import logger
from src.regime.detector import MarketRegimeDetector

router = APIRouter(
    prefix="/regime",
    tags=["Regime Detection"],
)

_detector: MarketRegimeDetector | None = None


def _get_detector() -> MarketRegimeDetector:
    global _detector
    if _detector is None:
        _detector = MarketRegimeDetector()
    return _detector


@router.get("/", status_code=status.HTTP_200_OK)
def detect_market_regime(
    symbol: str = Query(
        default=None,
        description="Optional stock symbol. If omitted, scans first available CSV.",
    ),
) -> dict[str, Any]:
    """Detect the current market regime for a given stock symbol.

    Analyses historical OHLCV data and returns the detected regime,
    confidence, supporting indicators, and human-readable reasons.

    Args:
        symbol: Optional stock symbol (e.g. 'NABIL'). When omitted the
            first available CSV file in the data directory is used.

    Returns:
        A dict containing the regime detection result.

    Raises:
        HTTPException: If symbol is invalid (400), data is missing
            (404), or detection fails (500).
    """
    data_dir = Path(DATA_DIRECTORY)

    if symbol:
        clean_symbol = symbol.strip().lower()
        if not clean_symbol or "/" in clean_symbol or "\\" in clean_symbol or ".." in clean_symbol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid stock symbol format: '{symbol}'",
            )
        file_path = data_dir / f"{clean_symbol}.csv"
        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stock data not found: {clean_symbol.upper()}",
            )
    else:
        csv_files = sorted(data_dir.glob("*.csv"))
        if not csv_files:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CSV data files found in data directory.",
            )
        file_path = csv_files[0]
        clean_symbol = file_path.stem

    try:
        logger.info("Loading data for regime detection: %s", file_path)
        df = load_csv(str(file_path))

        if df.empty:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Empty data file: {clean_symbol.upper()}",
            )

        detector = _get_detector()
        result = detector.detect(df)

        return {
            "symbol": clean_symbol.upper(),
            "regime": result.regime,
            "confidence": round(result.confidence, 2),
            "trend_strength": round(result.trend_strength, 4),
            "volatility": round(result.volatility, 4),
            "adx": round(result.adx, 2),
            "atr": round(result.atr, 6),
            "moving_average_slope": round(result.moving_average_slope, 6),
            "price_position": round(result.price_position, 6),
            "volume_strength": round(result.volume_strength, 2),
            "reasons": result.reasons,
            "metrics": result.metrics,
            "data_points": len(df),
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Error detecting regime for %s: %s", clean_symbol, err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to detect regime: {err}",
        ) from err
