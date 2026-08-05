# DEPRECATED — legacy ad-hoc smoke script (Sprint 10).
# Superseded by the real test suite in tests/. Remove in v1.1.
# TODO(v1.1): delete this file.
from loaders.csv_loader import load_csv
from indicators.moving_average import add_moving_averages
from indicators.momentum import add_momentum_indicators
from indicators.volume import add_volume_indicators
from signals.scorer import calculate_score, generate_signal


df = load_csv("data/raw/sample.csv")


df = add_moving_averages(df)
df = add_momentum_indicators(df)
df = add_volume_indicators(df)


latest = df.iloc[-1]


score = calculate_score(latest)

signal = generate_signal(score)


print("Score:", score)
print("Signal:", signal)