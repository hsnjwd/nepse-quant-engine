# DEPRECATED — legacy ad-hoc smoke script (Sprint 10).
# Superseded by the real test suite in tests/. Remove in v1.1.
# TODO(v1.1): delete this file.
from src.scanner.engine import scan_market

scan = scan_market()

stocks = scan["results"]
skipped = scan["skipped"]

print("=" * 60)
print(f"Analyzed : {len(stocks)}")
print(f"Skipped  : {len(skipped)}")
print("=" * 60)

if skipped:

    print("\nSkipped Files:\n")

    for item in skipped:

        print(
            f"{item['symbol']:8}"
            f"{item['error']}"
        )

print("\nTop 10 Stocks\n")

for stock in stocks[:10]:

    print(
        f"{stock['symbol']:8}"
        f"{stock['signal']:6}"
        f"Score: {stock['score']}"
    )