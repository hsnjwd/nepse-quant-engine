REQUIRED_COLUMNS = [
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume"
]


def validate_price_data(df):

    # Check columns
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            return False, f"Missing column: {col}"

    # Check invalid prices
    if (df["Close"] <= 0).any():
        return False, "Invalid closing prices"

    # Check volume
    if (df["Volume"] < 0).any():
        return False, "Invalid volume"

    return True, "Data validation passed"