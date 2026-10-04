import numpy as np
import pandas as pd


def add_bollinger_bands(df, period=20, inplace=False):
    """
    Calculate Bollinger Bands.

    Sprint 11.3: the middle band is ``Close.rolling(20).mean()`` which
    is value-identical to the ``SMA_20`` column the analyzer already
    computes first.  When both conditions hold (window == 20 and the
    ``SMA_20`` column exists) the rolling pass is reused instead of
    being recomputed — removing a duplicated window from the analysis
    hot path.  Standalone calls without an ``SMA_20`` column, or with a
    non-default window, compute the middle band exactly as before.

    Args:
        df: OHLCV DataFrame.
        period: Look-back window.
        inplace: When True, mutate and return the same object.

    Returns:
        DataFrame with BB_MIDDLE / BB_UPPER / BB_LOWER columns.
    """

    if not inplace:
        df = df.copy()

    if period == 20 and "SMA_20" in df.columns:
        middle = df["SMA_20"]
    else:
        middle = df["Close"].rolling(period).mean()
    std = df["Close"].rolling(period).std()

    df["BB_MIDDLE"] = middle
    df["BB_UPPER"] = middle + (2 * std)
    df["BB_LOWER"] = middle - (2 * std)

    return df



def add_atr(df, period=14, inplace=False):
    """
    Calculate Average True Range.

    Uses ``np.fmax`` to compute the element-wise maximum of the three
    true-range components without allocating a temporary DataFrame
    (the previous ``pd.concat(...).max(axis=1)`` created one per call).

    Args:
        df: OHLCV DataFrame.
        period: ATR look-back window.
        inplace: When True, mutate and return the same object.

    Returns:
        DataFrame with an ATR column.
    """

    if not inplace:
        df = df.copy()

    high_low = df["High"] - df["Low"]

    high_close = abs(
        df["High"] - df["Close"].shift()
    )

    low_close = abs(
        df["Low"] - df["Close"].shift()
    )

    true_range = np.fmax(
        np.fmax(high_low, high_close),
        low_close,
    )

    df["ATR"] = true_range.rolling(period).mean()

    return df



def add_volatility_indicators(df, inplace=False):

    df = add_bollinger_bands(df, inplace=inplace)
    df = add_atr(df, inplace=inplace)

    return df