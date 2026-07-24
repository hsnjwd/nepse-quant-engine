import math
from pathlib import Path
from typing import Any
from src.loaders.csv_loader import load_csv

from src.recommendations.trade_plan import create_trade_plan
from src.decision.confidence import calculate_confidence
from src.indicators.moving_average import add_moving_averages
from src.indicators.momentum import add_momentum_indicators
from src.indicators.volume import add_volume_indicators
from src.indicators.volatility import add_volatility_indicators
from src.risk.reward import calculate_risk_reward
from src.risk.position_size import calculate_position_size
from src.alerts.rules import check_alerts
from src.alerts.engine import process_alerts

from src.market_structure.levels import (
    get_support,
    get_resistance,
    get_trend,
)

from src.patterns.candlestick import (
    detect_pattern,
)

from src.signals.scorer import calculate_score

from src.decision.signal_engine import generate_signal


def safe_float(value: Any) -> float | None:
    """Convert a value to a float while preserving missing values.

    Args:
        value: Value to convert.

    Returns:
        The converted float, or ``None`` when the value is missing.
    """

    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    return float(value)


def analyze_dataframe(df: Any) -> dict[str, Any]:
    """Analyze an OHLCV data frame and return technical analysis results.

    Args:
        df: Price data containing the required OHLCV columns.

    Returns:
        Analysis values for the most recent candle.
    """
    df = add_moving_averages(df)
    df = add_momentum_indicators(df)
    df = add_volume_indicators(df)
    df = add_volatility_indicators(df)

    if df.empty:
        raise ValueError(
            "No rows remaining after indicator calculation."
        )

    latest = df.iloc[-1]

    support = get_support(df)

    resistance = get_resistance(df)

    trend = get_trend(latest)

    pattern = detect_pattern(df)

    score, breakdown = calculate_score(
        latest,
        pattern
    )

    confidence = calculate_confidence(
    score,
    breakdown
    )


    result = {
        "price": safe_float(latest["Close"]),
        "score": score,
        "confidence": confidence,

        "rsi": safe_float(latest["RSI"]),
        "macd": safe_float(latest["MACD"]),
        "atr": safe_float(latest["ATR"]),

        "trend": trend,
        "support": support,
        "resistance": resistance,

        "pattern": pattern["name"],
        "pattern_type": pattern["type"],
        "pattern_strength": pattern["strength"],
        "pattern_score": pattern["score"],

        "volume_signal": latest["VOLUME_SIGNAL"],
        "relative_volume": safe_float(latest["RELATIVE_VOLUME"]),
        "volume_score": int(latest["VOLUME_SCORE"]),

        "score_breakdown": breakdown,
    }

    result["signal"] = generate_signal(result)

    # Generate trade plan
    result.update(
        create_trade_plan(result)
    )

    # Calculate Risk/Reward
    result.update(
        calculate_risk_reward(result)
    )

    # Calculate Position Size
    result.update(
        calculate_position_size(result)
    )

    # Generate Alerts
    alerts = check_alerts(result)
    result["alerts"] = alerts
    return result



def analyze_stock(file: str) -> dict[str, Any]:
    """Load and analyze a stock CSV file.

    Args:
        file: Path to the source CSV file.

    Returns:
        Analysis values enriched with the stock symbol and new alerts.

    Raises:
        ValueError: If the CSV has no usable data.
    """

    df = load_csv(file)

    if df.empty:
        raise ValueError(
            "CSV contains no usable data."
        )

    result = analyze_dataframe(df)

    result["symbol"] = Path(file).stem.upper()

    result["new_alerts"] = process_alerts(
        result["symbol"],
        result
    )

    return result