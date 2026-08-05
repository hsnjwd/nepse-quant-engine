# DEPRECATED — legacy ad-hoc smoke script (Sprint 10).
# Superseded by the real test suite in tests/. Remove in v1.1.
# TODO(v1.1): delete this file.
from loaders.csv_loader import load_csv
from validators.data_validator import validate_price_data
from processors.cleaner import clean_price_data


file = "data/raw/sample.csv"


# Load data
df = load_csv(file)

print("\nOriginal Data:")
print(df)


# Validate
valid, message = validate_price_data(df)

print("\nValidation:")
print(message)


# Clean
if valid:
    cleaned = clean_price_data(df)

    print("\nCleaned Data:")
    print(cleaned)