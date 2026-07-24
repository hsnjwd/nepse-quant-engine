from typing import Any, Mapping


BASE_CONFIDENCE = 50
SCORE_WEIGHT = 4
TREND_WEIGHT = 3
MACD_WEIGHT = 3
VOLUME_WEIGHT = 4
PATTERN_WEIGHT = 5
RSI_WEIGHT = 2
MIN_CONFIDENCE = 0
MAX_CONFIDENCE = 100


def calculate_confidence(score: int | float, breakdown: Mapping[str, Any]) -> int:
    """
    Calculate confidence (0-100%)
    based on multiple technical factors.
    """

    confidence = BASE_CONFIDENCE

    # -------------------
    # Raw score
    # -------------------

    confidence += score * SCORE_WEIGHT

    # -------------------
    # Trend
    # -------------------

    confidence += breakdown["trend"] * TREND_WEIGHT

    # -------------------
    # MACD
    # -------------------

    confidence += breakdown["macd"] * MACD_WEIGHT

    # -------------------
    # Volume
    # -------------------

    confidence += breakdown["volume"] * VOLUME_WEIGHT

    # -------------------
    # Candlestick Pattern
    # -------------------

    confidence += breakdown["pattern"] * PATTERN_WEIGHT

    # -------------------
    # RSI
    # -------------------

    confidence += breakdown["rsi"] * RSI_WEIGHT

    # Clamp

    confidence = max(MIN_CONFIDENCE, confidence)
    confidence = min(MAX_CONFIDENCE, confidence)

    return round(confidence)