from loaders.csv_loader import load_csv
from indicators.momentum import add_momentum_indicators


df = load_csv("data/raw/sample.csv")

df = add_momentum_indicators(df)

print(df)