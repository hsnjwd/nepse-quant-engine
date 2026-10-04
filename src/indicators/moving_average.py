import pandas as pd


def add_moving_averages(df, inplace=False):
    """
    Add SMA and EMA indicators to OHLCV dataframe.

    Args:
        df: OHLCV DataFrame.
        inplace: When True, mutate and return the same object instead
            of copying (avoids redundant copies in the analysis chain).

    Returns:
        DataFrame with SMA_20 / SMA_50 / SMA_200 / EMA_20 columns.
    """

    if not inplace:
        df = df.copy()

    # Simple Moving Averages
    df["SMA_20"] = df["Close"].rolling(window=20).mean()
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    df["SMA_200"] = df["Close"].rolling(window=200).mean()

    # Exponential Moving Average
    df["EMA_20"] = df["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    return df