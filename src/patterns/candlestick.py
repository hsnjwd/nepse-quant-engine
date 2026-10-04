import math


def candle_size(row):
    return abs(row["Close"] - row["Open"])


def upper_shadow(row):
    return row["High"] - max(row["Open"], row["Close"])


def lower_shadow(row):
    return min(row["Open"], row["Close"]) - row["Low"]


def is_doji(row):

    body = candle_size(row)

    rng = row["High"] - row["Low"]

    if rng == 0:
        return False

    return body / rng < 0.10


def is_hammer(row):

    body = candle_size(row)

    upper = upper_shadow(row)

    lower = lower_shadow(row)

    return (
        lower > body * 2
        and upper < body
    )


def is_bullish_engulfing(previous, current):

    return (

        previous["Close"] < previous["Open"]

        and

        current["Close"] > current["Open"]

        and

        current["Open"] < previous["Close"]

        and

        current["Close"] > previous["Open"]

    )


def is_bearish_engulfing(previous, current):

    return (

        previous["Close"] > previous["Open"]

        and

        current["Close"] < current["Open"]

        and

        current["Open"] > previous["Close"]

        and

        current["Close"] < previous["Open"]

    )


def is_shooting_star(row):

    body = candle_size(row)

    upper = upper_shadow(row)

    lower = lower_shadow(row)

    return (
        upper > body * 2
        and lower < body
    )


def is_hanging_man(row):

    body = candle_size(row)

    upper = upper_shadow(row)

    lower = lower_shadow(row)

    return (
        lower > body * 2
        and upper < body
    )


def detect_pattern(df):

    if len(df) < 2:
        return {
            "name": None,
            "type": None,
            "strength": None,
            "score": 0,
        }

    current = df.iloc[-1]
    previous = df.iloc[-2]

    if is_bullish_engulfing(previous, current):
        return {
            "name": "Bullish Engulfing",
            "type": "Bullish",
            "strength": "Strong",
            "score": 2,
        }

    if is_bearish_engulfing(previous, current):
        return {
            "name": "Bearish Engulfing",
            "type": "Bearish",
            "strength": "Strong",
            "score": -2,
        }

    if is_hammer(current):
        return {
            "name": "Hammer",
            "type": "Bullish",
            "strength": "Medium",
            "score": 1,
        }

    if is_hanging_man(current):
        return {
            "name": "Hanging Man",
            "type": "Bearish",
            "strength": "Medium",
            "score": -1,
        }

    if is_shooting_star(current):
        return {
            "name": "Shooting Star",
            "type": "Bearish",
            "strength": "Medium",
            "score": -1,
        }

    if is_doji(current):
        return {
            "name": "Doji",
            "type": "Neutral",
            "strength": "Weak",
            "score": 0,
        }

    return {
        "name": None,
        "type": None,
        "strength": None,
        "score": 0,
    }