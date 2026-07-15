from fastapi import FastAPI
import math
from pathlib import Path
from fastapi import HTTPException

from src.loaders.csv_loader import load_csv

from src.indicators.moving_average import add_moving_averages
from src.indicators.momentum import add_momentum_indicators
from src.indicators.volume import add_volume_indicators
from src.indicators.volatility import add_volatility_indicators

from src.signals.scorer import (
    calculate_score,
    generate_signal
)


app = FastAPI(
    title="NEPSE Quant Engine API"
)


def safe_float(value):

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None

    return float(value)



def analyze_stock(file):

    df = load_csv(file)

    df = add_moving_averages(df)
    df = add_momentum_indicators(df)
    df = add_volume_indicators(df)
    df = add_volatility_indicators(df)

    latest = df.iloc[-1]

    score = calculate_score(latest)

    signal = generate_signal(score)

    return {
        "price": safe_float(latest["Close"]),
        "score": score,
        "signal": signal,
        "rsi": safe_float(latest["RSI"]),
        "macd": safe_float(latest["MACD"])
    }



@app.get("/")
def home():

    return {
        "status": "NEPSE Quant Engine Running"
    }



@app.get("/analyze/{symbol}")
def analyze(symbol: str):

    # Convert user input to lowercase
    symbol = symbol.lower()

    file_path = Path("data/raw") / f"{symbol}.csv"

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stock data not found: {symbol}"
        )

    return analyze_stock(str(file_path))