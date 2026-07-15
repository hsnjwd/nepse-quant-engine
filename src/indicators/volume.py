import pandas as pd


def add_volume_average(df, period=20):
    """
    Calculate average trading volume
    """

    df = df.copy()

    df["VOLUME_MA"] = (
        df["Volume"]
        .rolling(window=period)
        .mean()
    )

    return df



def add_volume_ratio(df):
    """
    Compare current volume with average volume
    """

    df = df.copy()

    df["VOLUME_RATIO"] = (
        df["Volume"] / df["VOLUME_MA"]
    )

    return df



def add_volume_signal(df):
    """
    Detect unusual volume activity
    """

    df = df.copy()

    conditions = [
        df["VOLUME_RATIO"] >= 2,
        df["VOLUME_RATIO"] >= 1.3,
        df["VOLUME_RATIO"] < 1
    ]

    choices = [
        "HIGH_VOLUME",
        "INCREASING_VOLUME",
        "LOW_VOLUME"
    ]

    df["VOLUME_SIGNAL"] = pd.Series(
        pd.cut(
            df["VOLUME_RATIO"],
            bins=[
                -float("inf"),
                1,
                1.3,
                float("inf")
            ],
            labels=choices
        ),
        index=df.index
    )

    return df



def add_volume_indicators(df):

    df = add_volume_average(df)
    df = add_volume_ratio(df)
    df = add_volume_signal(df)

    return df