import math

from src.loaders.csv_loader import load_csv

from src.indicators.moving_average import add_moving_averages
from src.indicators.momentum import add_momentum_indicators
from src.indicators.volume import add_volume_indicators
from src.indicators.volatility import add_volatility_indicators

from src.signals.scorer import (
    calculate_score,
    generate_signal,
)


def safe_float(value):

    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    return float(value)


def analyze_stock(file):

    df = load_csv(file)

    if df.empty:
        raise ValueError(
            "CSV contains no usable data."
        )

    df = add_moving_averages(df)
    df = add_momentum_indicators(df)
    df = add_volume_indicators(df)
    df = add_volatility_indicators(df)

    if df.empty:
        raise ValueError(
            "No rows remaining after indicator calculation."
        )

    latest = df.iloc[-1]

    score = calculate_score(latest)

    signal = generate_signal(score)

    return {
        "price": safe_float(latest["Close"]),
        "score": score,
        "signal": signal,
        "rsi": safe_float(latest["RSI"]),
        "macd": safe_float(latest["MACD"]),
    }