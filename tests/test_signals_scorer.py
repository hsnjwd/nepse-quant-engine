import pytest

from src.signals.scorer import calculate_score


@pytest.fixture
def pattern():
    return {"name": "Bullish Engulfing", "score": 2}


def test_calculate_score_returns_score_and_breakdown(pattern):
    row = {
        "Close": 110.0,
        "SMA_20": 100.0,
        "SMA_50": 90.0,
        "RSI": 50.0,
        "MACD": 1.3,
        "MACD_SIGNAL": 0.6,
        "VOLUME_SCORE": 1,
    }

    score, breakdown = calculate_score(row, pattern)

    assert score == 8
    assert breakdown["trend"] == 2
    assert breakdown["rsi"] == 1
    assert breakdown["macd"] == 2
    assert breakdown["volume"] == 1
    assert breakdown["pattern"] == 2
    assert breakdown["reasons"]


def test_calculate_score_handles_oversold_and_weak_volume(pattern):
    row = {
        "Close": 80.0,
        "SMA_20": 90.0,
        "SMA_50": 95.0,
        "RSI": 30.0,
        "MACD": 0.2,
        "MACD_SIGNAL": 0.6,
        "VOLUME_SCORE": -1,
    }

    score, breakdown = calculate_score(row, pattern)

    assert score == -1
    assert breakdown["trend"] == -2
    assert breakdown["rsi"] == 2
    assert breakdown["macd"] == -2
    assert breakdown["volume"] == -1
    assert breakdown["pattern"] == 2
