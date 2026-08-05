# DEPRECATED — legacy ad-hoc smoke script (Sprint 10).
# Superseded by the real test suite in tests/. Remove in v1.1.
# TODO(v1.1): delete this file.
from loaders.csv_loader import load_csv
from indicators.moving_average import add_moving_averages


file = "data/raw/sample.csv"


df = load_csv(file)

df = add_moving_averages(df)

print(df)