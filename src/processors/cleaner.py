import pandas as pd


def clean_price_data(df):
    """
    Clean NEPSE OHLCV data
    """

    df = df.copy()

    # Remove duplicate dates
    df = df.drop_duplicates(subset=["Date"])

    # Sort by date
    df = df.sort_values("Date")

    # Remove missing values
    df = df.dropna()

    # Reset index
    df = df.reset_index(drop=True)

    return df