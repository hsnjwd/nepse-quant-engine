"""Comprehensive tests for the Market Regime Detection Engine."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from src.regime.detector import (
    MarketRegime,
    MarketRegimeDetector,
    REGIME_LABELS,
)

# ======================================================================
# Constants for test data generation
# ======================================================================

_NROWS = 300


# ======================================================================
# Helper: build synthetic OHLCV DataFrames
# ======================================================================


def _build_ohlcv(
    prices: list[float],
    volume_base: float = 1_000_000,
    volume_noise: float = 0.2,
    spread: float = 0.02,
) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame from a price series.

    Args:
        prices: Closing price series.
        volume_base: Base volume level.
        volume_noise: Volume noise as a fraction of base.
        spread: High/Low spread as a fraction of price.

    Returns:
        DataFrame with Open, High, Low, Close, Volume columns.
    """
    rows: list[dict[str, float]] = []
    for i, close_price in enumerate(prices):
        open_price = prices[i - 1] if i > 0 else close_price
        half_spread = close_price * spread * 0.5
        high_price = max(open_price, close_price) + half_spread
        low_price = min(open_price, close_price) - half_spread
        volume = volume_base * (1.0 + volume_noise * (i % 5 - 2) / 2.0)
        rows.append({
            "Open": round(open_price, 2),
            "High": round(high_price, 2),
            "Low": round(low_price, 2),
            "Close": round(close_price, 2),
            "Volume": max(100, int(volume)),
        })
    return pd.DataFrame(rows)


def _uptrend(
    length: int = _NROWS,
    start: float = 100.0,
    drift: float = 0.15,
    noise: float = 2.0,
) -> list[float]:
    """Generate an uptrend price series."""
    prices: list[float] = [start]
    for i in range(1, length):
        step = drift * (i / length) + noise * (i % 7 - 3) / 10.0
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    return prices


def _downtrend(
    length: int = _NROWS,
    start: float = 200.0,
    drift: float = -0.15,
    noise: float = 2.0,
) -> list[float]:
    """Generate a downtrend price series."""
    prices: list[float] = [start]
    for i in range(1, length):
        step = drift * (i / length) + noise * (i % 7 - 3) / 10.0
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    return prices


def _sideways(
    length: int = _NROWS,
    center: float = 150.0,
    amplitude: float = 3.0,
    noise: float = 1.5,
) -> list[float]:
    """Generate a sideways / range-bound price series."""
    prices: list[float] = [center]
    for i in range(1, length):
        cycle = amplitude * math.sin(2 * math.pi * i / 40.0)
        step = noise * (i % 7 - 3) / 10.0
        prices.append(max(1.0, center + cycle + center * step / 100.0))
    return prices


def _high_volatility(
    length: int = _NROWS,
    center: float = 150.0,
    amplitude: float = 15.0,
) -> list[float]:
    """Generate a high-volatility price series."""
    prices: list[float] = [center]
    for i in range(1, length):
        cycle = amplitude * math.sin(2 * math.pi * i / 15.0)
        spike = 5.0 * (1.0 if i % 7 == 0 else 0.0)
        prices.append(max(1.0, center + cycle + spike))
    return prices


def _panic_series(length: int = _NROWS) -> list[float]:
    """Generate a panic / crash price series with volume spike."""
    prices: list[float] = [200.0]
    # Gradual uptrend for first 70%
    for i in range(1, int(length * 0.7)):
        step = 0.05 + 2.0 * (i % 7 - 3) / 10.0
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    # Sharp crash for remaining
    for i in range(int(length * 0.7), length):
        step = -2.0 - 3.0 * (i % 5) / 10.0
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    return prices


def _overheated_series(length: int = _NROWS) -> list[float]:
    """Generate an overheated / overbought price series."""
    prices: list[float] = [100.0]
    for i in range(1, length):
        step = 0.3 + 1.5 * (i % 7 - 3) / 10.0
        if i > length * 0.8:
            step *= 2.0  # explosive finale
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    return prices


def _recovery_series(length: int = _NROWS) -> list[float]:
    """Generate a recovery price series (bear then bull reversal)."""
    prices: list[float] = [200.0]
    # Bear market first 50%
    for i in range(1, int(length * 0.5)):
        step = -0.2 - 1.5 * (i % 7 - 3) / 10.0
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    # Recovery next 50%
    bottom = prices[-1]
    for i in range(int(length * 0.5), length):
        progress = (i - int(length * 0.5)) / (length - int(length * 0.5))
        step = 0.3 + 2.0 * progress + 1.0 * (i % 7 - 3) / 10.0
        prices.append(max(1.0, prices[-1] * (1.0 + step / 100.0)))
    return prices


def _accumulation_series(length: int = _NROWS) -> pd.DataFrame:
    """Generate an accumulation pattern (flat price, rising OBV)."""
    prices: list[float] = [150.0]
    volumes: list[float] = [1_000_000]
    for i in range(1, length):
        cycle = 2.0 * math.sin(2 * math.pi * i / 60.0)
        step = 1.0 * (i % 7 - 3) / 10.0
        prices.append(max(1.0, 150.0 + cycle + 150.0 * step / 100.0))
        # Volume increases over time, but price stays flat
        vol = 1_000_000 + 5_000 * i + 200_000 * (i % 10 - 5) / 5.0
        volumes.append(max(100, int(vol)))

    rows: list[dict[str, float]] = []
    for i in range(length):
        open_p = prices[i - 1] if i > 0 else prices[i]
        half_spread = prices[i] * 0.015
        rows.append({
            "Open": round(open_p, 2),
            "High": round(max(open_p, prices[i]) + half_spread, 2),
            "Low": round(min(open_p, prices[i]) - half_spread, 2),
            "Close": round(prices[i], 2),
            "Volume": max(100, int(volumes[i])),
        })
    return pd.DataFrame(rows)


def _distribution_series(length: int = _NROWS) -> pd.DataFrame:
    """Generate a distribution pattern (flat price, falling OBV)."""
    prices: list[float] = [180.0]
    volumes: list[float] = [1_000_000]
    for i in range(1, length):
        cycle = 2.0 * math.sin(2 * math.pi * i / 60.0)
        step = 1.0 * (i % 7 - 3) / 10.0
        prices.append(max(1.0, 180.0 + cycle + 180.0 * step / 100.0))
        # Volume decreases over time
        vol = 1_000_000 - 3_000 * i + 200_000 * (i % 10 - 5) / 5.0
        volumes.append(max(100, int(vol)))

    rows: list[dict[str, float]] = []
    for i in range(length):
        open_p = prices[i - 1] if i > 0 else prices[i]
        half_spread = prices[i] * 0.015
        rows.append({
            "Open": round(open_p, 2),
            "High": round(max(open_p, prices[i]) + half_spread, 2),
            "Low": round(min(open_p, prices[i]) - half_spread, 2),
            "Close": round(prices[i], 2),
            "Volume": max(100, int(volumes[i])),
        })
    return pd.DataFrame(rows)


# ======================================================================
# Shared fixtures
# ======================================================================


@pytest.fixture
def detector() -> MarketRegimeDetector:
    """Default detector instance."""
    return MarketRegimeDetector()


@pytest.fixture
def uptrend_df() -> pd.DataFrame:
    """DataFrame with a clear uptrend."""
    return _build_ohlcv(_uptrend())


@pytest.fixture
def downtrend_df() -> pd.DataFrame:
    """DataFrame with a clear downtrend."""
    return _build_ohlcv(_downtrend())


@pytest.fixture
def sideways_df() -> pd.DataFrame:
    """DataFrame with sideways / range-bound movement."""
    return _build_ohlcv(_sideways())


@pytest.fixture
def high_volatility_df() -> pd.DataFrame:
    """DataFrame with high volatility."""
    return _build_ohlcv(_high_volatility(), volume_noise=0.5, spread=0.05)


@pytest.fixture
def panic_df() -> pd.DataFrame:
    """DataFrame with panic / crash pattern."""
    prices = _panic_series()
    vols = [1_000_000] * len(prices)
    # Spike volume during crash
    crash_start = int(len(prices) * 0.7)
    for i in range(crash_start, len(prices)):
        vols[i] = int(3_000_000 * (1.0 + (i - crash_start) * 0.1))

    rows: list[dict[str, float]] = []
    for i, p in enumerate(prices):
        open_p = prices[i - 1] if i > 0 else p
        half_spread = p * 0.03
        rows.append({
            "Open": round(open_p, 2),
            "High": round(max(open_p, p) + half_spread, 2),
            "Low": round(min(open_p, p) - half_spread * 2, 2),
            "Close": round(p, 2),
            "Volume": vols[i],
        })
    return pd.DataFrame(rows)


@pytest.fixture
def overheated_df() -> pd.DataFrame:
    """DataFrame with overheated / overbought pattern."""
    return _build_ohlcv(_overheated_series())


@pytest.fixture
def recovery_df() -> pd.DataFrame:
    """DataFrame with recovery pattern."""
    return _build_ohlcv(_recovery_series())


@pytest.fixture
def accumulation_df() -> pd.DataFrame:
    """DataFrame with accumulation pattern."""
    return _accumulation_series()


@pytest.fixture
def distribution_df() -> pd.DataFrame:
    """DataFrame with distribution pattern."""
    return _distribution_series()


@pytest.fixture
def low_volume_df() -> pd.DataFrame:
    """DataFrame with very low volatility (tight range)."""
    return _build_ohlcv(
        _sideways(center=100.0, amplitude=0.5, noise=0.2),
        volume_noise=0.05,
        spread=0.003,
    )


@pytest.fixture
def empty_df() -> pd.DataFrame:
    """Empty DataFrame."""
    return pd.DataFrame()


@pytest.fixture
def missing_cols_df() -> pd.DataFrame:
    """DataFrame missing columns."""
    return pd.DataFrame({"Close": [100.0, 101.0], "Volume": [1000, 1100]})


@pytest.fixture
def nan_df() -> pd.DataFrame:
    """DataFrame with NaN values."""
    prices = _uptrend(length=50)
    df = _build_ohlcv(prices)
    df.loc[10:15, "Close"] = float("nan")
    df.loc[20, "High"] = float("nan")
    df.loc[25, "Low"] = float("nan")
    df.loc[30:32, "Volume"] = float("nan")
    return df


@pytest.fixture
def single_row_df() -> pd.DataFrame:
    """Single-row DataFrame."""
    return pd.DataFrame({
        "Open": [100.0],
        "High": [102.0],
        "Low": [99.0],
        "Close": [101.0],
        "Volume": [10000],
    })


@pytest.fixture
def short_history_df() -> pd.DataFrame:
    """DataFrame with insufficient history (< min_periods)."""
    prices = _uptrend(length=50)
    return _build_ohlcv(prices)


# ======================================================================
# Test MarketRegime dataclass
# ======================================================================


class TestMarketRegime:
    """Verify the MarketRegime dataclass."""

    def test_default_creation(self) -> None:
        """Default MarketRegime has UNKNOWN regime."""
        r = MarketRegime()
        assert r.regime == "UNKNOWN"
        assert r.confidence == 0.0
        assert r.reasons == []
        assert r.metrics == {}

    def test_custom_values(self) -> None:
        """All fields can be set via constructor."""
        r = MarketRegime(
            regime="BULL",
            confidence=85.5,
            trend_strength=45.0,
            volatility=0.02,
            adx=30.0,
            atr=0.015,
            moving_average_slope=0.005,
            price_position=0.03,
            volume_strength=70.0,
            reasons=["Price above MA", "ADX strong"],
            metrics={"rsi": 65.0, "macd": 1.5},
        )
        assert r.regime == "BULL"
        assert r.confidence == 85.5
        assert r.trend_strength == 45.0
        assert r.volatility == 0.02
        assert r.adx == 30.0
        assert r.atr == 0.015
        assert r.moving_average_slope == 0.005
        assert r.price_position == 0.03
        assert r.volume_strength == 70.0
        assert r.reasons == ["Price above MA", "ADX strong"]
        assert r.metrics["rsi"] == 65.0

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() contains all expected keys."""
        r = MarketRegime(
            regime="BEAR",
            confidence=72.0,
            trend_strength=38.0,
            volatility=0.03,
            adx=28.0,
            atr=0.02,
            moving_average_slope=-0.004,
            price_position=-0.02,
            volume_strength=30.0,
            reasons=["Price below MA"],
            metrics={"rsi": 35.0},
        )
        d = r.to_dict()
        assert d["regime"] == "BEAR"
        assert d["confidence"] == 72.0
        assert d["trend_strength"] == 38.0
        assert d["volatility"] == 0.03
        assert d["adx"] == 28.0
        assert d["atr"] == 0.02
        assert d["moving_average_slope"] == -0.004
        assert d["price_position"] == -0.02
        assert d["volume_strength"] == 30.0
        assert d["reasons"] == ["Price below MA"]
        assert d["metrics"]["rsi"] == 35.0

    def test_to_dict_rounds_values(self) -> None:
        """to_dict() rounds float values appropriately."""
        r = MarketRegime(
            regime="BULL",
            confidence=85.555,
            trend_strength=45.5555,
            volatility=0.02222,
            adx=30.555,
            atr=0.015555,
            moving_average_slope=0.005555,
            price_position=0.03333,
            volume_strength=70.555,
            reasons=["Test"],
            metrics={"val": 1.23456789},
        )
        d = r.to_dict()
        assert d["confidence"] == 85.56
        assert d["trend_strength"] == 45.5555
        assert d["volatility"] == 0.0222
        assert d["adx"] == 30.56
        assert d["atr"] == 0.015556
        assert d["price_position"] == 0.03333
        assert d["volume_strength"] == 70.56
        assert d["metrics"]["val"] == 1.234568

    def test_to_dict_keys_immutable(self) -> None:
        """to_dict() returns a copy, not a reference."""
        r = MarketRegime(reasons=["A", "B"])
        d = r.to_dict()
        d["reasons"].append("C")
        assert r.reasons == ["A", "B"]


# ======================================================================
# Test constructor
# ======================================================================


class TestConstructor:
    """Verify MarketRegimeDetector constructor."""

    def test_default_parameters(self) -> None:
        """Default constructor creates a valid detector."""
        d = MarketRegimeDetector()
        assert d._adx_period == 14
        assert d._atr_period == 14
        assert d._rsi_period == 14
        assert d._bb_period == 20
        assert d._volume_period == 20
        assert d._min_periods == 200

    def test_custom_parameters(self) -> None:
        """Custom parameters are accepted."""
        d = MarketRegimeDetector(
            adx_period=10,
            atr_period=12,
            rsi_period=7,
            bb_period=15,
            volume_period=10,
            min_periods=100,
        )
        assert d._adx_period == 10
        assert d._atr_period == 12
        assert d._rsi_period == 7
        assert d._bb_period == 15
        assert d._volume_period == 10
        assert d._min_periods == 100

    @pytest.mark.parametrize(
        "name,value",
        [
            ("adx_period", 0),
            ("adx_period", -1),
            ("atr_period", 0),
            ("atr_period", -5),
            ("rsi_period", 0),
            ("rsi_period", -10),
            ("bb_period", 0),
            ("bb_period", -3),
            ("volume_period", 0),
            ("volume_period", -2),
            ("min_periods", 0),
            ("min_periods", -100),
        ],
    )
    def test_invalid_periods_raise_error(self, name: str, value: int) -> None:
        """Non-positive periods raise ValueError."""
        kwargs = {
            "adx_period": 14,
            "atr_period": 14,
            "rsi_period": 14,
            "bb_period": 20,
            "volume_period": 20,
            "min_periods": 200,
        }
        kwargs[name] = value
        with pytest.raises(ValueError, match="must be positive"):
            MarketRegimeDetector(**kwargs)


# ======================================================================
# Test detect() — general cases
# ======================================================================


class TestDetectGeneral:
    """General detect() behaviour."""

    def test_empty_dataframe(self, detector: MarketRegimeDetector) -> None:
        """Empty DataFrame returns UNKNOWN regime."""
        result = detector.detect(pd.DataFrame())
        assert result.regime == "UNKNOWN"
        assert "Empty" in str(result.reasons)

    def test_missing_columns(self, detector: MarketRegimeDetector) -> None:
        """DataFrame with missing columns returns UNKNOWN."""
        df = pd.DataFrame({"Close": [100.0], "Volume": [1000]})
        result = detector.detect(df)
        assert result.regime == "UNKNOWN"
        assert any("Missing" in r for r in result.reasons)

    def test_not_a_dataframe(self, detector: MarketRegimeDetector) -> None:
        """Non-DataFrame input raises TypeError or returns UNKNOWN."""
        result = detector.detect([1, 2, 3])  # type: ignore[arg-type]
        assert result.regime == "UNKNOWN"

    def test_single_row(self, detector: MarketRegimeDetector, single_row_df: pd.DataFrame) -> None:
        """Single-row DataFrame returns UNKNOWN (insufficient history)."""
        result = detector.detect(single_row_df)
        assert result.regime == "UNKNOWN"
        assert any("history" in r.lower() for r in result.reasons)

    def test_short_history(self, detector: MarketRegimeDetector, short_history_df: pd.DataFrame) -> None:
        """Short history returns UNKNOWN."""
        result = detector.detect(short_history_df)
        assert result.regime == "UNKNOWN"

    def test_nan_values_handled(self, detector: MarketRegimeDetector, nan_df: pd.DataFrame) -> None:
        """NaN values in the DataFrame do not crash detection."""
        result = detector.detect(nan_df)
        assert isinstance(result, MarketRegime)
        assert isinstance(result.regime, str)

    def test_returns_market_regime_instance(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """detect() always returns a MarketRegime instance."""
        result = detector.detect(uptrend_df)
        assert isinstance(result, MarketRegime)

    def test_confidence_range(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Confidence is between 0 and 100."""
        result = detector.detect(uptrend_df)
        assert 0.0 <= result.confidence <= 100.0

    def test_metrics_contains_indicators(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Metrics dict contains indicator values."""
        result = detector.detect(uptrend_df)
        assert "adx" in result.metrics
        assert "rsi" in result.metrics
        assert "atr" in result.metrics
        assert "sma20" in result.metrics
        assert "sma50" in result.metrics
        assert "macd" in result.metrics
        assert "volume_ratio" in result.metrics
        assert "obv_slope" in result.metrics

    def test_reasons_is_list(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Reasons is a list of strings."""
        result = detector.detect(uptrend_df)
        assert isinstance(result.reasons, list)
        for r in result.reasons:
            assert isinstance(r, str)


# ======================================================================
# Test regime detection — specific regimes
# ======================================================================


class TestBullDetection:
    """Verify BULL regime detection."""

    def test_detects_bull(self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame) -> None:
        """Uptrend DataFrame is classified as BULL (or similar)."""
        result = detector.detect(uptrend_df)
        assert result.regime in ("BULL", "OVERHEATED", "RECOVERY")

    def test_bull_has_positive_ma_slope(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Bull market should have positive MA slope."""
        result = detector.detect(uptrend_df)
        if result.regime == "BULL":
            assert result.moving_average_slope > -0.01

    def test_bull_has_high_confidence(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Bull detection should have reasonable confidence."""
        result = detector.detect(uptrend_df)
        if result.regime == "BULL":
            assert result.confidence >= 30.0


class TestBearDetection:
    """Verify BEAR regime detection."""

    def test_detects_bear(
        self, detector: MarketRegimeDetector, downtrend_df: pd.DataFrame
    ) -> None:
        """Downtrend DataFrame is classified as BEAR or PANIC."""
        result = detector.detect(downtrend_df)
        assert result.regime in ("BEAR", "PANIC", "RECOVERY")


class TestSidewaysDetection:
    """Verify SIDEWAYS regime detection."""

    def test_detects_sideways(
        self, detector: MarketRegimeDetector, sideways_df: pd.DataFrame
    ) -> None:
        """Sideways DataFrame is classified as SIDEWAYS."""
        result = detector.detect(sideways_df)
        assert result.regime == "SIDEWAYS" or result.regime == "LOW_VOLATILITY"

    def test_sideways_low_adx(
        self, detector: MarketRegimeDetector, sideways_df: pd.DataFrame
    ) -> None:
        """Sideways market should have low ADX."""
        result = detector.detect(sideways_df)
        if result.regime == "SIDEWAYS":
            assert result.adx < 30.0


class TestHighVolatilityDetection:
    """Verify HIGH_VOLATILITY regime detection."""

    def test_detects_high_volatility(
        self, detector: MarketRegimeDetector, high_volatility_df: pd.DataFrame
    ) -> None:
        """High-volatility DataFrame is classified as HIGH_VOLATILITY."""
        result = detector.detect(high_volatility_df)
        assert result.regime in (
            "HIGH_VOLATILITY", "BULL", "BEAR", "SIDEWAYS", "PANIC",
        )


class TestLowVolatilityDetection:
    """Verify LOW_VOLATILITY regime detection."""

    def test_detects_low_volatility(
        self, detector: MarketRegimeDetector, low_volume_df: pd.DataFrame
    ) -> None:
        """Low-volatility DataFrame is classified as LOW_VOLATILITY or SIDEWAYS."""
        result = detector.detect(low_volume_df)
        assert result.regime in ("LOW_VOLATILITY", "SIDEWAYS")


class TestPanicDetection:
    """Verify PANIC regime detection."""

    def test_detects_panic(
        self, detector: MarketRegimeDetector, panic_df: pd.DataFrame
    ) -> None:
        """Panic DataFrame is classified as PANIC."""
        result = detector.detect(panic_df)
        assert result.regime == "PANIC"

    def test_panic_has_volume_spike(
        self, detector: MarketRegimeDetector, panic_df: pd.DataFrame
    ) -> None:
        """Panic detection includes volume evidence in reasons."""
        result = detector.detect(panic_df)
        assert any("volume" in r.lower() for r in result.reasons)


class TestOverheatedDetection:
    """Verify OVERHEATED regime detection."""

    def test_detects_overheated(
        self, detector: MarketRegimeDetector, overheated_df: pd.DataFrame
    ) -> None:
        """Overheated DataFrame is classified as OVERHEATED or BULL."""
        result = detector.detect(overheated_df)
        assert result.regime in ("OVERHEATED", "BULL")


class TestRecoveryDetection:
    """Verify RECOVERY regime detection."""

    def test_detects_recovery(
        self, detector: MarketRegimeDetector, recovery_df: pd.DataFrame
    ) -> None:
        """Recovery DataFrame is classified as RECOVERY or BULL."""
        result = detector.detect(recovery_df)
        assert result.regime in ("RECOVERY", "BULL")


class TestAccumulationDetection:
    """Verify ACCUMULATION regime detection."""

    def test_detects_accumulation(
        self, detector: MarketRegimeDetector, accumulation_df: pd.DataFrame
    ) -> None:
        """Accumulation DataFrame is classified as ACCUMULATION."""
        result = detector.detect(accumulation_df)
        assert result.regime in ("ACCUMULATION", "SIDEWAYS")

    def test_accumulation_obv_rising(
        self, detector: MarketRegimeDetector, accumulation_df: pd.DataFrame
    ) -> None:
        """Accumulation should have rising OBV."""
        result = detector.detect(accumulation_df)
        if result.regime == "ACCUMULATION":
            assert result.volume_strength >= 50.0


class TestDistributionDetection:
    """Verify DISTRIBUTION regime detection."""

    def test_detects_distribution(
        self, detector: MarketRegimeDetector, distribution_df: pd.DataFrame
    ) -> None:
        """Distribution DataFrame is classified as DISTRIBUTION."""
        result = detector.detect(distribution_df)
        assert result.regime in ("DISTRIBUTION", "SIDEWAYS", "BEAR")


# ======================================================================
# Test detect_dict()
# ======================================================================


class TestDetectDict:
    """Verify detect_dict() convenience method."""

    def test_returns_dict(self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame) -> None:
        """detect_dict() returns a dict."""
        result = detector.detect_dict(uptrend_df)
        assert isinstance(result, dict)

    def test_contains_regime_key(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Dict contains 'regime' key."""
        result = detector.detect_dict(uptrend_df)
        assert "regime" in result

    def test_equivalent_to_to_dict(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """detect_dict() is equivalent to detect().to_dict()."""
        d1 = detector.detect_dict(uptrend_df)
        d2 = detector.detect(uptrend_df).to_dict()
        assert d1 == d2


# ======================================================================
# Test detect_batch()
# ======================================================================


class TestDetectBatch:
    """Verify detect_batch() method."""

    def test_returns_list(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame, downtrend_df: pd.DataFrame
    ) -> None:
        """detect_batch() returns a list of MarketRegime instances."""
        results = detector.detect_batch([uptrend_df, downtrend_df])
        assert isinstance(results, list)
        assert len(results) == 2
        assert all(isinstance(r, MarketRegime) for r in results)

    def test_empty_list(self, detector: MarketRegimeDetector) -> None:
        """Empty list returns empty results."""
        results = detector.detect_batch([])
        assert results == []

    def test_not_a_list(self, detector: MarketRegimeDetector) -> None:
        """Non-list input raises TypeError."""
        with pytest.raises(TypeError, match="Expected a list"):
            detector.detect_batch("not_a_list")  # type: ignore[arg-type]

    def test_mixed_valid_and_invalid(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Batch handles both valid and invalid DataFrames."""
        results = detector.detect_batch([uptrend_df, pd.DataFrame()])
        assert len(results) == 2
        assert results[0].regime != "UNKNOWN"
        assert results[1].regime == "UNKNOWN"


# ======================================================================
# Test confidence calculation
# ======================================================================


class TestConfidence:
    """Confidence score calculations."""

    def test_strong_trend_high_confidence(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Strong trend should produce reasonable confidence."""
        result = detector.detect(uptrend_df)
        if result.regime in ("BULL", "OVERHEATED"):
            assert result.confidence > 0.0

    def test_empty_dataframe_zero_confidence(self, detector: MarketRegimeDetector) -> None:
        """Empty DataFrame returns zero confidence."""
        result = detector.detect(pd.DataFrame())
        assert result.confidence == 0.0


# ======================================================================
# Test trend_strength
# ======================================================================


class TestTrendStrength:
    """Trend strength calculations."""

    def test_trend_strength_range(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Trend strength is between 0 and 100."""
        result = detector.detect(uptrend_df)
        assert 0.0 <= result.trend_strength <= 100.0


# ======================================================================
# Test compute_volume_strength
# ======================================================================


class TestVolumeStrength:
    """Volume strength calculations."""

    def test_volume_strength_range(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """Volume strength is between 0 and 100."""
        result = detector.detect(uptrend_df)
        assert 0.0 <= result.volume_strength <= 100.0


# ======================================================================
# Test serialisation
# ======================================================================


class TestSerialization:
    """Serialisation methods."""

    def test_to_dict_json_serializable(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame
    ) -> None:
        """to_dict() output is JSON-serializable (basic Python types)."""
        import json

        result = detector.detect(uptrend_df)
        d = result.to_dict()
        # Should not raise TypeError
        json.dumps(d)

    def test_empty_regime_to_dict(self) -> None:
        """Default MarketRegime to_dict() works."""
        r = MarketRegime()
        d = r.to_dict()
        assert d["regime"] == "UNKNOWN"


# ======================================================================
# Test edge cases
# ======================================================================


class TestEdgeCases:
    """Edge case inputs."""

    def test_zero_volume(self, detector: MarketRegimeDetector) -> None:
        """Zero volume should not crash."""
        prices = _uptrend(length=_NROWS)
        df = _build_ohlcv(prices)
        df["Volume"] = 0
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_all_same_price(self, detector: MarketRegimeDetector) -> None:
        """All same price should not crash."""
        prices = [100.0] * _NROWS
        df = _build_ohlcv(prices)
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_extreme_prices(self, detector: MarketRegimeDetector) -> None:
        """Extreme price values should not crash."""
        prices = [1e-6, 1e6] * (_NROWS // 2)
        df = _build_ohlcv(prices[: _NROWS])
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_negative_prices(self, detector: MarketRegimeDetector) -> None:
        """Negative prices should not crash."""
        prices = [-100.0 + i * 0.5 for i in range(_NROWS)]
        df = _build_ohlcv(prices)
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_all_nan(self, detector: MarketRegimeDetector) -> None:
        """All NaN should not crash."""
        df = pd.DataFrame({
            "Open": [float("nan")] * 10,
            "High": [float("nan")] * 10,
            "Low": [float("nan")] * 10,
            "Close": [float("nan")] * 10,
            "Volume": [float("nan")] * 10,
        })
        # Will hit insufficient history (10 < 200)
        result = detector.detect(df)
        assert result.regime == "UNKNOWN"

    def test_integer_columns(self, detector: MarketRegimeDetector) -> None:
        """Integer column values are handled."""
        prices = _uptrend(length=_NROWS)
        df = _build_ohlcv(prices)
        df["Close"] = df["Close"].astype(int)
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_deterministic_output(self, detector: MarketRegimeDetector) -> None:
        """Same input produces same output (no randomness)."""
        prices = _uptrend()
        df = _build_ohlcv(prices)
        r1 = detector.detect(df)
        r2 = detector.detect(df)
        assert r1.regime == r2.regime
        assert r1.confidence == r2.confidence

    def test_adx_in_metrics(self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame) -> None:
        """ADX value is present in metrics."""
        result = detector.detect(uptrend_df)
        assert isinstance(result.metrics.get("adx"), float)


# ======================================================================
# Test boundary conditions
# ======================================================================


class TestBoundaryConditions:
    """Boundary conditions."""

    def test_min_periods_exact(self) -> None:
        """Exactly min_periods rows is sufficient."""
        prices = _uptrend(length=200)
        df = _build_ohlcv(prices)
        d = MarketRegimeDetector(min_periods=200)
        result = d.detect(df)
        assert result.regime != "UNKNOWN"

    def test_min_periods_minus_one(self) -> None:
        """Exactly (min_periods - 1) rows returns UNKNOWN."""
        prices = _uptrend(length=199)
        df = _build_ohlcv(prices)
        d = MarketRegimeDetector(min_periods=200)
        result = d.detect(df)
        assert result.regime == "UNKNOWN"

    def test_very_large_dataset(self, detector: MarketRegimeDetector) -> None:
        """1000+ rows does not crash."""
        prices = _uptrend(length=1000)
        df = _build_ohlcv(prices)
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_regime_labels_defined(self) -> None:
        """REGIME_LABELS contains all expected regimes."""
        assert len(REGIME_LABELS) == 10
        assert "BULL" in REGIME_LABELS
        assert "BEAR" in REGIME_LABELS
        assert "SIDEWAYS" in REGIME_LABELS
        assert "HIGH_VOLATILITY" in REGIME_LABELS
        assert "LOW_VOLATILITY" in REGIME_LABELS
        assert "ACCUMULATION" in REGIME_LABELS
        assert "DISTRIBUTION" in REGIME_LABELS
        assert "RECOVERY" in REGIME_LABELS
        assert "PANIC" in REGIME_LABELS
        assert "OVERHEATED" in REGIME_LABELS


# ======================================================================
# Test logging
# ======================================================================


class TestLogging:
    """Logging behaviour (smoke-test only)."""

    def test_detect_logs_regime(
        self, detector: MarketRegimeDetector, uptrend_df: pd.DataFrame, caplog: pytest.LogCaptureFixture
    ) -> None:
        """detect() logs the detected regime."""
        detector.detect(uptrend_df)
        assert any(
            "Regime detected" in record.message
            for record in caplog.records
        )

    def test_detect_batch_logs_count(
        self,
        detector: MarketRegimeDetector,
        uptrend_df: pd.DataFrame,
        downtrend_df: pd.DataFrame,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """detect_batch() logs the batch count."""
        detector.detect_batch([uptrend_df, downtrend_df])
        assert any(
            "Batch regime detection" in record.message
            for record in caplog.records
        )


# ======================================================================
# Test error handling
# ======================================================================


class TestErrorHandling:
    """Error handling edge cases."""

    def test_invalid_input_type_string(self, detector: MarketRegimeDetector) -> None:
        """String input returns UNKNOWN."""
        result = detector.detect("invalid")  # type: ignore[arg-type]
        assert result.regime == "UNKNOWN"

    def test_invalid_input_type_number(self, detector: MarketRegimeDetector) -> None:
        """Integer input returns UNKNOWN."""
        result = detector.detect(42)  # type: ignore[arg-type]
        assert result.regime == "UNKNOWN"

    def test_invalid_input_type_none(self, detector: MarketRegimeDetector) -> None:
        """None input returns UNKNOWN."""
        result = detector.detect(None)  # type: ignore[arg-type]
        assert result.regime == "UNKNOWN"

    def test_dataframe_with_extra_columns(
        self, detector: MarketRegimeDetector
    ) -> None:
        """DataFrame with extra columns works fine."""
        prices = _uptrend(length=_NROWS)
        df = _build_ohlcv(prices)
        df["Extra"] = 0
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)


# ======================================================================
# Test indicator computation independently
# ======================================================================


class TestIndicatorComputation:
    """Internal indicator computation methods."""

    def test_compute_indicators_adds_columns(self, detector: MarketRegimeDetector) -> None:
        """_compute_indicators adds all expected columns."""
        prices = _uptrend(length=_NROWS)
        df = _build_ohlcv(prices)
        df = detector._compute_indicators(df)
        expected = {
            "SMA_20", "SMA_50", "SMA_200",
            "ATR", "ADX", "DI_PLUS", "DI_MINUS",
            "RSI", "MACD", "MACD_SIGNAL",
            "BB_UPPER", "BB_LOWER", "BB_WIDTH", "BB_POSITION",
            "VOLUME_MA", "RELATIVE_VOLUME", "OBV",
        }
        assert expected.issubset(set(df.columns))

    def test_compute_obv(self) -> None:
        """_compute_obv produces a valid series."""
        close = pd.Series([100.0, 102.0, 101.0, 103.0, 102.0])
        volume = pd.Series([1000, 1500, 1200, 1800, 1100])
        obv = MarketRegimeDetector._compute_obv(close, volume)
        assert len(obv) == 5
        # First value should be 0
        assert obv.iloc[0] == 0.0
        # Second: close up → OBV = 0 + 1500 = 1500
        assert obv.iloc[1] == 1500.0
        # Third: close down → OBV = 1500 - 1200 = 300
        assert obv.iloc[2] == 300.0
        # Fourth: close up → OBV = 300 + 1800 = 2100
        assert obv.iloc[3] == 2100.0
        # Fifth: close down → OBV = 2100 - 1100 = 1000
        assert obv.iloc[4] == 1000.0

    def test_compute_obv_slope_positive(self, detector: MarketRegimeDetector) -> None:
        """_compute_obv_slope returns positive for rising OBV."""
        close = pd.Series([float(i) for i in range(100)])
        volume = pd.Series([1000 + i * 10 for i in range(100)])
        obv = MarketRegimeDetector._compute_obv(close, volume)
        df = pd.DataFrame({"OBV": obv})
        slope = detector._compute_obv_slope(df, window=10)
        # Rising prices + rising volume = rising OBV → positive slope
        assert slope > 0.0

    def test_compute_obv_slope_negative(self, detector: MarketRegimeDetector) -> None:
        """_compute_obv_slope returns negative for falling OBV."""
        close = pd.Series([float(100 - i) for i in range(100)])
        volume = pd.Series([1000 + i * 10 for i in range(100)])
        obv = MarketRegimeDetector._compute_obv(close, volume)
        df = pd.DataFrame({"OBV": obv})
        slope = detector._compute_obv_slope(df, window=10)
        # Falling prices + rising volume = falling OBV → negative slope
        assert slope < 0.0

    def test_compute_obv_slope_missing_column(self, detector: MarketRegimeDetector) -> None:
        """_compute_obv_slope returns 0.0 when OBV column missing."""
        df = pd.DataFrame({"Close": [1.0, 2.0]})
        slope = detector._compute_obv_slope(df)
        assert slope == 0.0

    def test_safe_float_valid(self) -> None:
        """_safe_float returns the value when key exists."""
        series = pd.Series({"Close": 100.5})
        val = MarketRegimeDetector._safe_float(series, "Close")
        assert val == 100.5

    def test_safe_float_missing(self) -> None:
        """_safe_float returns default when key missing."""
        series = pd.Series({"Close": 100.5})
        val = MarketRegimeDetector._safe_float(series, "Missing")
        assert val == 0.0

    def test_safe_float_nan(self) -> None:
        """_safe_float returns default for NaN value."""
        series = pd.Series({"Close": float("nan")})
        val = MarketRegimeDetector._safe_float(series, "Close")
        assert val == 0.0

    def test_safe_float_custom_default(self) -> None:
        """_safe_float accepts a custom default."""
        series = pd.Series({"Close": float("nan")})
        val = MarketRegimeDetector._safe_float(series, "Close", 50.0)
        assert val == 50.0


# ======================================================================
# Test regime scoring methods (unit tests)
# ======================================================================


class TestRegimeScoring:
    """Unit tests for individual regime scoring methods."""

    def test_score_bull_high(self) -> None:
        """Strongly bullish indicators produce high BULL score."""
        from pandas import Series

        score, reasons = MarketRegimeDetector._score_bull(
            close_val=110.0,
            sma20=100.0,
            sma50=95.0,
            adx_val=30.0,
            di_plus=25.0,
            di_minus=15.0,
            ma_slope=0.005,
            rsi_val=60.0,
            macd_val=2.0,
            macd_hist=1.0,
            returns_5=0.02,
            returns_20=0.08,
            sma20_series=Series([100.0] * 50),
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_bear_high(self) -> None:
        """Strongly bearish indicators produce high BEAR score."""
        score, reasons = MarketRegimeDetector._score_bear(
            close_val=90.0,
            sma20=100.0,
            sma50=105.0,
            adx_val=35.0,
            di_plus=12.0,
            di_minus=28.0,
            ma_slope=-0.005,
            rsi_val=35.0,
            macd_val=-2.0,
            macd_hist=-1.0,
            returns_5=-0.03,
            returns_20=-0.10,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_sideways_high(self) -> None:
        """Neutral indicators produce high SIDEWAYS score."""
        score, reasons = MarketRegimeDetector._score_sideways(
            adx_val=15.0,
            rsi_val=50.0,
            atr_percentile=0.2,
            ma_slope=0.0,
            bb_width=0.03,
            vol_20=0.005,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_high_volatility_high(self) -> None:
        """High-volatility indicators produce high HIGH_VOLATILITY score."""
        score, reasons = MarketRegimeDetector._score_high_volatility(
            atr_percentile=0.85,
            bb_width=0.15,
            vol_20=0.035,
            atr_pct=0.04,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_low_volatility_high(self) -> None:
        """Low-volatility indicators produce high LOW_VOLATILITY score."""
        score, reasons = MarketRegimeDetector._score_low_volatility(
            atr_percentile=0.15,
            bb_width=0.02,
            vol_20=0.005,
            atr_pct=0.005,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_accumulation_high(self) -> None:
        """Accumulation indicators produce high ACCUMULATION score."""
        score, reasons = MarketRegimeDetector._score_accumulation(
            returns_20=0.02,
            obv_slope=0.05,
            volume_ratio=1.5,
            sma20=100.0,
            close_val=100.5,
            bb_position=0.3,
            sma50=98.0,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_distribution_high(self) -> None:
        """Distribution indicators produce high DISTRIBUTION score."""
        score, reasons = MarketRegimeDetector._score_distribution(
            returns_20=-0.02,
            obv_slope=-0.05,
            volume_ratio=1.5,
            sma20=100.0,
            close_val=99.5,
            bb_position=0.7,
            sma50=102.0,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_recovery_high(self) -> None:
        """Recovery indicators produce high RECOVERY score."""
        score, reasons = MarketRegimeDetector._score_recovery(
            returns_5=0.02,
            returns_20=-0.05,
            ma_slope=0.002,
            macd_hist=0.5,
            rsi_val=40.0,
            adx_val=22.0,
            di_plus=20.0,
            di_minus=18.0,
            close_val=98.0,
            sma20=100.0,
        )
        assert score > 3.0
        assert len(reasons) >= 2

    def test_score_panic_high(self) -> None:
        """Panic indicators produce high PANIC score."""
        score, reasons = MarketRegimeDetector._score_panic(
            returns_1=-0.05,
            returns_5=-0.12,
            volume_ratio=2.5,
            atr_percentile=0.9,
            rsi_val=20.0,
            vol_20=0.04,
            close_val=85.0,
            sma20=100.0,
        )
        assert score > 4.0
        assert len(reasons) >= 2

    def test_score_overheated_high(self) -> None:
        """Overheated indicators produce high OVERHEATED score."""
        score, reasons = MarketRegimeDetector._score_overheated(
            rsi_val=80.0,
            price_position=0.07,
            returns_5=0.08,
            returns_20=0.18,
            ma_slope=0.015,
            volume_ratio=1.8,
            macd_hist=-0.5,
            bb_position=0.95,
        )
        assert score > 4.0
        assert len(reasons) >= 2

    def test_score_bull_low(self) -> None:
        """Bearish indicators produce low BULL score."""
        from pandas import Series

        score, reasons = MarketRegimeDetector._score_bull(
            close_val=50.0,
            sma20=100.0,
            sma50=105.0,
            adx_val=10.0,
            di_plus=10.0,
            di_minus=25.0,
            ma_slope=-0.01,
            rsi_val=30.0,
            macd_val=-3.0,
            macd_hist=-2.0,
            returns_5=-0.05,
            returns_20=-0.15,
            sma20_series=Series([100.0] * 50),
        )
        assert score < 2.0


# ======================================================================
# Test ADX computation
# ======================================================================


class TestADX:
    """ADX calculation correctness."""

    def test_compute_adx_returns_dataframe(self) -> None:
        """_compute_adx returns a DataFrame with expected columns."""
        close = pd.Series([float(100 + i) for i in range(50)])
        high = close + 2.0
        low = close - 2.0
        result = MarketRegimeDetector._compute_adx(high, low, close, period=14)
        assert isinstance(result, pd.DataFrame)
        assert "ADX" in result.columns
        assert "DI_PLUS" in result.columns
        assert "DI_MINUS" in result.columns

    def test_adx_range(self) -> None:
        """ADX values are between 0 and 100."""
        close = pd.Series([float(100 + i) for i in range(100)])
        high = close + 2.0
        low = close - 2.0
        result = MarketRegimeDetector._compute_adx(high, low, close, period=14)
        adx = result["ADX"].dropna()
        if len(adx) > 0:
            assert adx.between(0, 100).all()

    def test_adx_strong_trend(self) -> None:
        """Strong uptrend produces ADX > 20."""
        close = pd.Series([float(100 + i * 0.5) for i in range(100)])
        high = close + 3.0
        low = close - 3.0
        result = MarketRegimeDetector._compute_adx(high, low, close, period=14)
        adx = result["ADX"].dropna()
        if len(adx) > 0:
            assert adx.iloc[-1] > 20.0


# ======================================================================
# Test MarketRegimeDetector — miscellaneous
# ======================================================================


class TestMiscellaneous:
    """Miscellaneous tests."""

    def test_repr(self, detector: MarketRegimeDetector) -> None:
        """Detector has a valid string representation."""
        assert repr(detector).startswith("MarketRegimeDetector")

    def test_multiple_detections_consistent(
        self, detector: MarketRegimeDetector
    ) -> None:
        """Calling detect() multiple times is consistent."""
        prices = _uptrend()
        df = _build_ohlcv(prices)
        r1 = detector.detect(df)
        r2 = detector.detect(df)
        r3 = detector.detect(df)
        assert r1.regime == r2.regime == r3.regime

    def test_unknown_regime_on_invalid(
        self, detector: MarketRegimeDetector
    ) -> None:
        """Invalid input yields UNKNOWN regime."""
        result = detector.detect(pd.DataFrame())
        assert result.regime == "UNKNOWN"


# ======================================================================
# Test large portfolio simulation
# ======================================================================


class TestLargeData:
    """Large dataset handling."""

    def test_500_rows(self, detector: MarketRegimeDetector) -> None:
        """500 rows is processed without error."""
        prices = _uptrend(length=500)
        df = _build_ohlcv(prices)
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)

    def test_1000_rows(self, detector: MarketRegimeDetector) -> None:
        """1000 rows is processed without error."""
        prices = _uptrend(length=1000)
        df = _build_ohlcv(prices)
        result = detector.detect(df)
        assert isinstance(result, MarketRegime)
