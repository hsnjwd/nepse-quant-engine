import pandas as pd


def add_bollinger_bands(df, period=20):
    """
    Calculate Bollinger Bands
    """

    df = df.copy()

    middle = df["Close"].rolling(period).mean()
    std = df["Close"].rolling(period).std()

    df["BB_MIDDLE"] = middle
    df["BB_UPPER"] = middle + (2 * std)
    df["BB_LOWER"] = middle - (2 * std)

    return df



def add_atr(df, period=14):
    """
    Calculate Average True Range
    """

    df = df.copy()

    high_low = df["High"] - df["Low"]

    high_close = abs(
        df["High"] - df["Close"].shift()
    )

    low_close = abs(
        df["Low"] - df["Close"].shift()
    )

    true_range = pd.concat(
        [
            high_low,
            high_close,
            low_close
        ],
        axis=1
    ).max(axis=1)

    df["ATR"] = true_range.rolling(period).mean()

    return df



def add_volatility_indicators(df):

    df = add_bollinger_bands(df)
    df = add_atr(df)

    return df