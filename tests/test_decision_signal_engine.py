import pytest

from src.decision.signal_engine import generate_signal


@pytest.mark.parametrize(
    "result, expected",
    [
        (
            {
                "score": 5,
                "trend": "UPTREND",
                "rsi": 50,
                "volume_signal": "NORMAL",
                "score_breakdown": {"macd": 1, "trend": 1},
            },
            "BUY",
        ),
        (
            {
                "score": -4,
                "trend": "DOWNTREND",
                "rsi": 50,
                "volume_signal": "LOW_VOLUME",
                "score_breakdown": {"macd": -1, "trend": -1},
            },
            "SELL",
        ),
        (
            {
                "score": 1,
                "trend": "SIDEWAYS",
                "rsi": 80,
                "volume_signal": "LOW_VOLUME",
                "score_breakdown": {"macd": 0, "trend": 0},
            },
            "HOLD",
        ),
    ],
)
def test_generate_signal(result, expected):
    assert generate_signal(result) == expected
