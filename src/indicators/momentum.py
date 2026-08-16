import pandas as pd


def add_rsi(df, period=14, inplace=False):
    """
    Calculate RSI indicator.

    Args:
        df: OHLCV DataFrame.
        period: RSI look-back period.
        inplace: When True, mutate and return the same object instead
            of copying.

    Returns:
        DataFrame with an RSI column.
    """

    if not inplace:
        df = df.copy()

    delta = df["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss

    df["RSI"] = 100 - (100 / (1 + rs))

    return df



def add_macd(df, inplace=False):
    """
    Calculate MACD indicator.

    Args:
        df: OHLCV DataFrame.
        inplace: When True, mutate and return the same object instead
            of copying.

    Returns:
        DataFrame with MACD and MACD_SIGNAL columns.
    """

    if not inplace:
        df = df.copy()

    ema12 = df["Close"].ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = df["Close"].ewm(
        span=26,
        adjust=False
    ).mean()

    df["MACD"] = ema12 - ema26

    df["MACD_SIGNAL"] = df["MACD"].ewm(
        span=9,
        adjust=False
    ).mean()

    return df



def add_momentum_indicators(df, inplace=False):

    df = add_rsi(df, inplace=inplace)
    df = add_macd(df, inplace=inplace)

    return df