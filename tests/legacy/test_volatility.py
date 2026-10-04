# DEPRECATED — legacy ad-hoc smoke script (Sprint 10).
# Superseded by the real test suite in tests/. Remove in v1.1.
# TODO(v1.1): delete this file.
from loaders.csv_loader import load_csv
from indicators.volatility import add_volatility_indicators


df = load_csv("data/raw/sample.csv")

df = add_volatility_indicators(df)

print(df)