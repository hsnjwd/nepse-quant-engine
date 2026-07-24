from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.engine.analyzer import analyze_stock
from src.config import DATA_DIRECTORY

router = APIRouter()


@router.get("/{symbol}")
def analyze(symbol: str):

    symbol = symbol.lower()

    file_path = Path(DATA_DIRECTORY) / f"{symbol}.csv"

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stock data not found: {symbol}"
        )

    result = analyze_stock(str(file_path))

    result["symbol"] = symbol.upper()

    return result