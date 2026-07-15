import pandas as pd


def load_csv(file_path):

    df = pd.read_csv(file_path)


    # Clean column names
    df.columns = df.columns.str.strip()


    # Convert numeric columns
    numeric_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]


    for col in numeric_columns:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype(str)
                .str.replace(",", "", regex=False)
            )

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )


    # Sort by date
    if "Date" in df.columns:

        df["Date"] = pd.to_datetime(df["Date"])

        df = df.sort_values(
            "Date"
        )


    # Remove invalid rows

    df = df.dropna(
        subset=["Close"]
    )


    return df