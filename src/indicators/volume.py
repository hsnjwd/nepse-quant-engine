import pandas as pd


def add_volume_average(df, period=20, inplace=False):
    """
    Calculate average trading volume.

    Args:
        df: OHLCV DataFrame.
        period: Look-back window.
        inplace: When True, mutate and return the same object.

    Returns:
        DataFrame with a VOLUME_MA column.
    """

    if not inplace:
        df = df.copy()

    df["VOLUME_MA"] = (
        df["Volume"]
        .rolling(period)
        .mean()
    )

    return df


def add_volume_ratio(df, inplace=False):
    """
    Relative Volume (RVOL).

    Args:
        df: OHLCV DataFrame.
        inplace: When True, mutate and return the same object.

    Returns:
        DataFrame with a RELATIVE_VOLUME column.
    """

    if not inplace:
        df = df.copy()

    df["RELATIVE_VOLUME"] = (
        df["Volume"]
            / df["VOLUME_MA"]
    )
    return df

"""
Run all volume indicators.
"""
def add_volume_signal(df, inplace=False):
    """
    Detect unusual volume activity.

    Args:
        df: OHLCV DataFrame.
        inplace: When True, mutate and return the same object.

    Returns:
        DataFrame with a VOLUME_SIGNAL column.
    """

    if not inplace:
        df = df.copy()

    df["VOLUME_SIGNAL"] = pd.cut(
        df["RELATIVE_VOLUME"],
        bins=[
            -float("inf"),
            0.7,
            1.3,
            2.0,
            float("inf")
        ],
        labels=[
            "LOW_VOLUME",
            "NORMAL_VOLUME",
            "HIGH_VOLUME",
            "VOLUME_SPIKE"
        ]
    )

    return df


def add_volume_score(df, inplace=False):
    """
    Convert volume activity into a score.

    Args:
        df: OHLCV DataFrame.
        inplace: When True, mutate and return the same object.

    Returns:
        DataFrame with a VOLUME_SCORE column.
    """

    if not inplace:
        df = df.copy()

    score_map = {
        "LOW_VOLUME": -1,
        "NORMAL_VOLUME": 0,
        "HIGH_VOLUME": 1,
        "VOLUME_SPIKE": 2,
    }

    df["VOLUME_SCORE"] = (
        df["VOLUME_SIGNAL"]
        .map(score_map)
        .fillna(0)
        .astype(int)
    )

    return df


def add_volume_indicators(df, inplace=False):

    df = add_volume_average(df, inplace=inplace)
    df = add_volume_ratio(df, inplace=inplace)
    df = add_volume_signal(df, inplace=inplace)
    df = add_volume_score(df, inplace=inplace)

    return df