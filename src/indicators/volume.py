import pandas as pd


def add_volume_average(df, period=20):
    """
    Calculate average trading volume.
    """

    df = df.copy()

    df["VOLUME_MA"] = (
        df["Volume"]
        .rolling(period)
        .mean()
    )

    return df


def add_volume_ratio(df):
    """
    Relative Volume (RVOL)
    """

    df = df.copy()

    df["RELATIVE_VOLUME"] = (
        df["Volume"]
            / df["VOLUME_MA"]
    )
    return df

"""
Run all volume indicators.
"""
def add_volume_signal(df):
    """
    Detect unusual volume activity.
    """

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


def add_volume_score(df):
    """
    Convert volume activity into a score.
    """

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


def add_volume_indicators(df):

    df = add_volume_average(df)
    df = add_volume_ratio(df)
    df = add_volume_signal(df)
    df = add_volume_score(df)

    return df