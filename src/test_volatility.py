from loaders.csv_loader import load_csv
from indicators.volatility import add_volatility_indicators


df = load_csv("data/raw/sample.csv")

df = add_volatility_indicators(df)

print(df)