# ML Guide

The ML subsystem (`src/ml/`) provides supervised learning for NEPSE
trading signals with graceful degradation when optional libraries are
unavailable.

## Quick Start

```python
from src.ml import TrainingPipeline, PredictionEngine

# 1. Train on a symbol's OHLCV history
history = DataService().get_history("NABIL", days=500)
result = TrainingPipeline().run(
    history.df,
    model_name="random_forest",
    task="classification",
    horizon=5,
)
print(result.metrics)

# 2. Predict the next signal
prediction = PredictionEngine().predict(history.df, model_name="random_forest")
print(prediction.signal, prediction.probability)
```

## Feature Engineering

`FeatureEngineer` automatically generates these feature families:

| Family | Columns |
| --- | --- |
| Returns | RETURN_1D/5D/20D, LOG_RETURN_1D |
| Momentum | RSI, MACD, MACD_SIGNAL, MACD_HIST, ROC_10/20, MOMENTUM_10/20 |
| Moving averages | SMA_5..200, EMA_5..200 |
| Volatility | ATR, ATR_PCT, VOLATILITY_20/60, CLOSE_ZSCORE_20 |
| Bollinger | BB_MID/UPPER/LOWER, BB_WIDTH, BB_POSITION |
| Trend | ADX, DI_PLUS, DI_MINUS |
| Volume | OBV, OBV_SMA_20, CMF, VOLUME_RATIO, VOLUME_TREND |
| Price | VWAP, PRICE_VS_VWAP, PRICE_VS_SMA20/50, SMA20_VS_SMA50 |
| Rolling | VOLATILITY_20/60, CLOSE_ZSCORE_20 |
| Regime | REGIME_UPTREND, REGIME_DOWNTREND, ... |
| Lag | CLOSE_LAG_1..5, RETURN_LAG_1/2 |

Customize with `FeatureConfig(features=[...])`. Unknown feature names
raise `ValueError`.

## Models

`MODEL_REGISTRY` maps names to wrappers:

| Registry name | Backing library | Fallback |
| --- | --- | --- |
| `random_forest` | sklearn | NumpyLogisticRegression |
| `gradient_boosting` | sklearn | NumpyLogisticRegression |
| `logistic_regression` | sklearn | NumpyLogisticRegression |
| `xgboost` | xgboost | sklearn gradient boosting → numpy |
| `lightgbm` | lightgbm | sklearn gradient boosting → numpy |
| `numpy_logistic` | numpy | — |
| `numpy_ridge` | numpy | — |

`default_model_name()` picks the best available implementation.

## Datasets

- `DatasetBuilder.build_classification(df, horizon, buy_threshold, sell_threshold)`
  → labels `{0: SELL, 1: HOLD, 2: BUY}` from forward returns.
- `DatasetBuilder.build_regression(df, horizon)` → forward-return target.
- `DatasetBuilder.split(dataset, test_size, shuffle)` — time-ordered by
  default (no shuffle), which suits financial data.

## Cross-Validation

`TimeSeriesSplitter` provides rolling and expanding windows without
look-ahead leakage. `cross_validate(model, dataset, n_splits)` returns
per-fold metrics and a best fold.

## Versioning & Persistence

```python
manager = ModelManager("models")
record = manager.save(model, name="random_forest", metrics=metrics)
loaded = manager.load("random_forest")
```

Versions follow semantic versioning (`1.0.0` → `1.0.1` patch bumps) and
are tracked in `models/versions/`. `ModelVersion`, `VersionStore`,
`bump_version`, and `validate_version` are exported from
`src.ml.versioning`.

## Prediction

`PredictionEngine.predict(df, model_name=..., task=..., horizon=...)`
returns a `PredictionResult` with:

- `signal` — BUY / HOLD / SELL (classification)
- `probability` — max class probability
- `predicted_return` — regression forecast
- `feature_importances`, `raw_proba`, `model_version`

## Dependencies

- scikit-learn, xgboost, lightgbm are optional.
- The subsystem always works with pure numpy fallbacks.
