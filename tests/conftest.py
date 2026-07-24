import pandas as pd
import pytest


@pytest.fixture
def sample_frame():
    return pd.DataFrame(
        [
            {
                "Close": 100.0,
                "SMA_20": 95.0,
                "SMA_50": 90.0,
                "RSI": 55.0,
                "MACD": 1.5,
                "MACD_SIGNAL": 0.8,
                "VOLUME_SIGNAL": "NORMAL",
                "RELATIVE_VOLUME": 1.2,
                "VOLUME_SCORE": 1,
                "ATR": 2.5,
            }
        ]
    )


@pytest.fixture
def sample_breakdown():
    return {
        "trend": 1,
        "rsi": 1,
        "macd": 2,
        "volume": 1,
        "pattern": 3,
        "reasons": ["healthy"],
    }


@pytest.fixture
def base_result():
    return {
        "signal": "BUY",
        "score": 5,
        "confidence": 90,
        "trend": "UPTREND",
        "volume_signal": "VOLUME_SPIKE",
        "relative_volume": 1.8,
        "pattern_type": "Bullish",
        "pattern": "Bullish Engulfing",
        "price": 100.0,
        "target1": 110.0,
        "target2": 120.0,
        "target3": 130.0,
        "milestones": {
            "target1": False,
            "target2": False,
            "target3": False,
        },
        "best_rr": 3.5,
    }
