import pytest

from src.decision.confidence import calculate_confidence


@pytest.mark.parametrize(
    "score, breakdown, expected",
    [
        (0, {"trend": 0, "macd": 0, "volume": 0, "pattern": 0, "rsi": 0}, 50),
        (1, {"trend": 1, "macd": 1, "volume": 1, "pattern": 1, "rsi": 1}, 50 + 4 + 3 + 3 + 4 + 5 + 2),
        (-10, {"trend": -5, "macd": -5, "volume": -5, "pattern": -5, "rsi": -5}, 0),
    ],
)
def test_calculate_confidence(score, breakdown, expected):
    assert calculate_confidence(score, breakdown) == expected
