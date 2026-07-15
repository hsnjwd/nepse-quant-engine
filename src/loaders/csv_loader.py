import pandas as pd


def load_csv(file_path):
    """
    Load NEPSE historical price CSV file
    """

    df = pd.read_csv(file_path)

    df["Date"] = pd.to_datetime(df["Date"])

    df = df.sort_values("Date")

    return df


if __name__ == "__main__":
    data = load_csv("data/raw/sample.csv")
    print(data.head())