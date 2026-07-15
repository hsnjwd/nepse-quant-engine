from loaders.csv_loader import load_csv
from indicators.volume import add_volume_indicators


df = load_csv("data/raw/sample.csv")

df = add_volume_indicators(df)

print(df)