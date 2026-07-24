from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.backtest.engine import backtest
from src.config import DATA_DIRECTORY

router = APIRouter()


@router.get("/{symbol}")
def run_backtest(symbol: str):

    symbol = symbol.lower()

    file_path = Path(DATA_DIRECTORY) / f"{symbol}.csv"

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stock data not found: {symbol}"
        )

    return backtest(str(file_path))