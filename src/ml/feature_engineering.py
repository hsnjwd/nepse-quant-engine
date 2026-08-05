"""Feature engineering for the NEPSE Quant Engine ML subsystem.

Automatically generates technical features from an OHLCV DataFrame:

* Momentum: RSI, MACD, ROC, momentum
* Trend: SMA/EMA at multiple periods
* Volatility: ATR, Bollinger Bands, rolling std
* Volume: volume ratio, OBV, CMF, VWAP
* Returns, rolling statistics and lag features
* Regime features (price vs moving averages)

Every feature generator is a named function registered in
``FEATURE_REGISTRY`` so feature sets are configurable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.ml.utils import require_dataframe

logger = logging.getLogger("nepse.ml.features")


# ---------------------------------------------------------------------------
# Individual feature generators — each takes a DataFrame and returns it
# with additional columns appended.
# ---------------------------------------------------------------------------


def _add_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Daily returns and log returns."""
    df["RETURN_1D"] = df["Close"].pct_change()
    df["LOG_RETURN_1D"] = np.log(df["Close"] / df["Close"].shift(1))
    df["RETURN_5D"] = df["Close"].pct_change(5)
    df["RETURN_20D"] = df["Close"].pct_change(20)
    return df


def _add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Relative Strength Index."""
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    return df


def _add_macd(df: pd.DataFrame) -> pd.DataFrame:
    """MACD line, signal and histogram."""
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]
    return df


def _add_sma_ema(df: pd.DataFrame) -> pd.DataFrame:
    """Simple and exponential moving averages."""
    for period in (5, 10, 20, 50, 100, 200):
        df[f"SMA_{period}"] = df["Close"].rolling(period).mean()
        df[f"EMA_{period}"] = df["Close"].ewm(span=period, adjust=False).mean()
    return df


def _add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average True Range."""
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(period).mean()
    df["ATR_PCT"] = df["ATR"] / df["Close"]
    return df


def _add_bollinger(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Bollinger Bands and band position."""
    mid = df["Close"].rolling(period).mean()
    std = df["Close"].rolling(period).std()
    df["BB_MID"] = mid
    df["BB_UPPER"] = mid + 2 * std
    df["BB_LOWER"] = mid - 2 * std
    df["BB_WIDTH"] = (df["BB_UPPER"] - df["BB_LOWER"]) / mid
    df["BB_POSITION"] = (df["Close"] - df["BB_LOWER"]) / (
        df["BB_UPPER"] - df["BB_LOWER"]
    ).replace(0, np.nan)
    return df


def _add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index."""
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift()).abs(),
            (df["Low"] - df["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False
    ).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False
    ).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.ewm(alpha=1 / period, adjust=False).mean()
    df["DI_PLUS"] = plus_di
    df["DI_MINUS"] = minus_di
    return df


def _add_obv(df: pd.DataFrame) -> pd.DataFrame:
    """On-Balance Volume."""
    direction = np.sign(df["Close"].diff()).fillna(0)
    df["OBV"] = (direction * df["Volume"]).cumsum()
    df["OBV_SMA_20"] = df["OBV"].rolling(20).mean()
    return df


def _add_cmf(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Chaikin Money Flow."""
    high_low = (df["High"] - df["Low"]).replace(0, np.nan)
    mfv = (
        ((df["Close"] - df["Low"]) - (df["High"] - df["Close"]))
        / high_low
        * df["Volume"]
    )
    df["CMF"] = mfv.rolling(period).sum() / df["Volume"].rolling(period).sum()
    return df


def _add_roc(df: pd.DataFrame) -> pd.DataFrame:
    """Rate of change."""
    df["ROC_10"] = df["Close"].pct_change(10)
    df["ROC_20"] = df["Close"].pct_change(20)
    return df


def _add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Volume-weighted average price (daily)."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    df["VWAP"] = (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()
    df["PRICE_VS_VWAP"] = df["Close"] / df["VWAP"]
    return df


def _add_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Relative volume and volume trends."""
    df["VOLUME_MA_20"] = df["Volume"].rolling(20).mean()
    df["VOLUME_RATIO"] = df["Volume"] / df["VOLUME_MA_20"].replace(0, np.nan)
    df["VOLUME_MA_5"] = df["Volume"].rolling(5).mean()
    df["VOLUME_TREND"] = df["VOLUME_MA_5"] / df["VOLUME_MA_20"].replace(
        0, np.nan
    )
    return df


def _add_rolling_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling volatility and z-scores."""
    df["VOLATILITY_20"] = df["Close"].pct_change().rolling(20).std()
    df["VOLATILITY_60"] = df["Close"].pct_change().rolling(60).std()
    df["CLOSE_ZSCORE_20"] = (
        df["Close"] - df["Close"].rolling(20).mean()
    ) / df["Close"].rolling(20).std().replace(0, np.nan)
    return df


def _add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Lag features of close and volume."""
    for lag in (1, 2, 3, 5):
        df[f"CLOSE_LAG_{lag}"] = df["Close"].shift(lag)
    df["RETURN_LAG_1"] = df["RETURN_1D"].shift(1)
    df["RETURN_LAG_2"] = df["RETURN_1D"].shift(2)
    return df


def _add_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """Price momentum relative to moving averages."""
    df["MOMENTUM_10"] = df["Close"] / df["Close"].shift(10) - 1
    df["MOMENTUM_20"] = df["Close"] / df["Close"].shift(20) - 1
    df["PRICE_VS_SMA20"] = df["Close"] / df["SMA_20"]
    df["PRICE_VS_SMA50"] = df["Close"] / df["SMA_50"]
    df["SMA20_VS_SMA50"] = df["SMA_20"] / df["SMA_50"]
    return df


def _add_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """Simple rule-based regime proxies."""
    df["REGIME_SMA20_ABOVE_SMA50"] = (df["SMA_20"] > df["SMA_50"]).astype(float)
    df["REGIME_PRICE_ABOVE_SMA50"] = (df["Close"] > df["SMA_50"]).astype(float)
    df["REGIME_UPTREND"] = (
        (df["Close"] > df["SMA_20"]) & (df["SMA_20"] > df["SMA_50"])
    ).astype(float)
    df["REGIME_DOWNTREND"] = (
        (df["Close"] < df["SMA_20"]) & (df["SMA_20"] < df["SMA_50"])
    ).astype(float)
    return df


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FEATURE_REGISTRY: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "returns": _add_returns,
    "rsi": _add_rsi,
    "macd": _add_macd,
    "moving_averages": _add_sma_ema,
    "atr": _add_atr,
    "bollinger": _add_bollinger,
    "adx": _add_adx,
    "obv": _add_obv,
    "cmf": _add_cmf,
    "roc": _add_roc,
    "vwap": _add_vwap,
    "volume_ratio": _add_volume_ratio,
    "rolling_stats": _add_rolling_stats,
    "momentum": _add_momentum,
    "regime": _add_regime_features,
}

DEFAULT_FEATURES: tuple[str, ...] = (
    "returns",
    "rsi",
    "macd",
    "moving_averages",
    "atr",
    "bollinger",
    "adx",
    "obv",
    "cmf",
    "roc",
    "vwap",
    "volume_ratio",
    "rolling_stats",
    "momentum",
    "regime",
)

# Ordered so that generators that depend on earlier columns run after them.
_EXECUTION_ORDER: tuple[str, ...] = (
    "returns",
    "rsi",
    "macd",
    "moving_averages",
    "atr",
    "bollinger",
    "adx",
    "obv",
    "cmf",
    "roc",
    "vwap",
    "volume_ratio",
    "rolling_stats",
    "momentum",
    "regime",
    "lag",
)

# Lag features depend on returns.
FEATURE_REGISTRY["lag"] = _add_lag_features


@dataclass
class FeatureConfig:
    """Configuration for the :class:`FeatureEngineer`.

    Attributes:
        features: Ordered subset of ``FEATURE_REGISTRY`` names.
        dropna: Drop rows containing NaN after feature generation.
        min_rows: Minimum number of rows required after cleaning.
    """

    features: list[str] = field(
        default_factory=lambda: list(DEFAULT_FEATURES)
    )
    dropna: bool = True
    min_rows: int = 10

    def __post_init__(self) -> None:
        """Validate the feature names."""
        unknown = [f for f in self.features if f not in FEATURE_REGISTRY]
        if unknown:
            raise ValueError(
                f"Unknown features: {unknown}. "
                f"Available: {sorted(FEATURE_REGISTRY)}"
            )


class FeatureEngineer:
    """Generate technical features from OHLCV data.

    Usage::

        engineer = FeatureEngineer(FeatureConfig())
        features = engineer.generate(df)
    """

    def __init__(self, config: FeatureConfig | None = None) -> None:
        """Initialise the engineer.

        Args:
            config: Optional feature configuration.  Defaults to all
                features.
        """
        self._config = config or FeatureConfig()

    @property
    def config(self) -> FeatureConfig:
        """Return the active feature configuration."""
        return self._config

    def generate(self, df: Any) -> pd.DataFrame:
        """Generate features from an OHLCV DataFrame.

        Args:
            df: DataFrame with ``Open``/``High``/``Low``/``Close``/
                ``Volume`` columns.

        Returns:
            DataFrame with the configured feature columns appended.

        Raises:
            ValueError: If fewer than ``min_rows`` rows remain.
        """
        require_dataframe(df)
        result = df.copy()

        for name in _EXECUTION_ORDER:
            if name not in self._config.features:
                continue
            fn = FEATURE_REGISTRY[name]
            try:
                result = fn(result)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Feature generator '%s' failed: %s", name, exc)

        if self._config.dropna:
            result = result.dropna()

        if len(result) < self._config.min_rows:
            raise ValueError(
                f"Only {len(result)} rows remain after feature "
                f"engineering; need at least {self._config.min_rows}."
            )

        logger.info(
            "Generated %d feature columns from %d rows.",
            len(self.feature_columns(result)),
            len(result),
        )
        return result

    def feature_columns(self, df: Any) -> list[str]:
        """Return the names of generated feature columns.

        Numeric columns present in the returned frame but not in the
        base OHLCV frame are treated as features.  Non-numeric columns
        (e.g. ``Date``) are always excluded.

        Args:
            df: The engineered DataFrame.

        Returns:
            Sorted list of numeric feature column names.
        """
        base = {"Open", "High", "Low", "Close", "Volume"}
        numeric = {
            col
            for col in df.columns
            if pd.api.types.is_numeric_dtype(df[col])
        }
        return sorted(col for col in numeric if col not in base)

    def list_features(self) -> list[str]:
        """Return the configured feature group names."""
        return list(self._config.features)
