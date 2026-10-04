import pandas as pd


def get_support(df: pd.DataFrame, lookback: int = 20):

    recent = df.tail(lookback)

    return round(
        float(recent["Low"].min()),
        2
    )


def get_resistance(df: pd.DataFrame, lookback: int = 20):

    recent = df.tail(lookback)

    return round(
        float(recent["High"].max()),
        2
    )


def get_trend(latest):

    sma20 = latest["SMA_20"]
    sma50 = latest["SMA_50"]
    close = latest["Close"]

    if close > sma20 > sma50:
        return "UPTREND"

    if close < sma20 < sma50:
        return "DOWNTREND"

    return "SIDEWAYS"
