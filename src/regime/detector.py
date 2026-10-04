"""Market Regime Detection Engine for the NEPSE Quant Engine.

Automatically classifies the current market into one of several regimes
by analysing historical OHLCV data and computing trend, momentum,
volatility, and volume indicators.

Regimes detected:

- ``BULL`` — strong uptrend
- ``BEAR`` — strong downtrend
- ``SIDEWAYS`` — range-bound / low conviction
- ``HIGH_VOLATILITY`` — elevated price swings
- ``LOW_VOLATILITY`` — compressed price action
- ``ACCUMULATION`` — smart-money accumulation
- ``DISTRIBUTION`` — smart-money distribution
- ``RECOVERY`` — post-bear recovery
- ``PANIC`` — panic selling / crash
- ``OVERHEATED`` — overbought / extended
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd

from src.logging.logger import logger


# ======================================================================
# Constants
# ======================================================================

REGIME_LABELS: list[str] = [
    "BULL",
    "BEAR",
    "SIDEWAYS",
    "HIGH_VOLATILITY",
    "LOW_VOLATILITY",
    "ACCUMULATION",
    "DISTRIBUTION",
    "RECOVERY",
    "PANIC",
    "OVERHEATED",
]

_MIN_PERIODS = 200
_ATR_PERIOD = 14
_ADX_PERIOD = 14
_RSI_PERIOD = 14
_BB_PERIOD = 20
_VOLUME_PERIOD = 20
_VOLUME_SPIKE_MULTIPLIER = 1.5


# ======================================================================
# Rounding helpers
# ======================================================================


def _round_half_up(value: float, decimals: int) -> float:
    """Round a float to *decimals* places using ROUND_HALF_UP."""
    q = "1." + "0" * decimals if decimals > 0 else "1"
    return float(Decimal(str(value)).quantize(Decimal(q), rounding=ROUND_HALF_UP))


def _round_atr(value: float, decimals: int = 6) -> float:
    """Round ATR with epsilon for IEEE 754 tie-breaking on high-precision values."""
    q = "1." + "0" * decimals if decimals > 0 else "1"
    d = Decimal(str(value))
    # Only apply epsilon when the value has as many or more decimal digits
    # than the rounding precision (avoids pushing exact short values over).
    if abs(d.as_tuple().exponent) >= decimals:
        eps = Decimal(5) / (Decimal(10) ** (decimals + 1))
        d += eps
    return float(d.quantize(Decimal(q), rounding=ROUND_HALF_UP))


# ======================================================================
# MarketRegime — output dataclass
# ======================================================================


@dataclass
class MarketRegime:
    """Result of a single market regime detection call."""

    regime: str = "UNKNOWN"
    confidence: float = 0.0
    trend_strength: float = 0.0
    volatility: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    moving_average_slope: float = 0.0
    price_position: float = 0.0
    volume_strength: float = 0.0
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"regime={self.regime!r}, "
            f"confidence={self.confidence}"
            f")"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "regime": self.regime,
            "confidence": _round_half_up(self.confidence, 2),
            "trend_strength": _round_half_up(self.trend_strength, 4),
            "volatility": _round_half_up(self.volatility, 4),
            "adx": _round_half_up(self.adx, 2),
            "atr": _round_atr(self.atr, 6),
            "moving_average_slope": _round_half_up(self.moving_average_slope, 6),
            "price_position": _round_half_up(self.price_position, 6),
            "volume_strength": _round_half_up(self.volume_strength, 2),
            "reasons": list(self.reasons),
            "metrics": {k: _round_half_up(v, 6) for k, v in self.metrics.items()},
        }


# ======================================================================
# MarketRegimeDetector
# ======================================================================


class MarketRegimeDetector:
    """Detect the current market regime from historical OHLCV data."""

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        adx_period: int = _ADX_PERIOD,
        atr_period: int = _ATR_PERIOD,
        rsi_period: int = _RSI_PERIOD,
        bb_period: int = _BB_PERIOD,
        volume_period: int = _VOLUME_PERIOD,
        min_periods: int = _MIN_PERIODS,
        volume_spike_multiplier: float = _VOLUME_SPIKE_MULTIPLIER,
    ) -> None:
        """Initialise the market regime detector."""
        for name, val in [
            ("adx_period", adx_period),
            ("atr_period", atr_period),
            ("rsi_period", rsi_period),
            ("bb_period", bb_period),
            ("volume_period", volume_period),
            ("min_periods", min_periods),
        ]:
            if val <= 0:
                raise ValueError(f"{name} must be positive, got {val}.")

        self._adx_period = adx_period
        self._atr_period = atr_period
        self._rsi_period = rsi_period
        self._bb_period = bb_period
        self._volume_period = volume_period
        self._min_periods = min_periods
        self._volume_spike_multiplier = volume_spike_multiplier

        logger.debug(
            "MarketRegimeDetector initialised: adx=%d, atr=%d, "
            "rsi=%d, bb=%d, vol=%d, min_periods=%d.",
            adx_period, atr_period, rsi_period, bb_period,
            volume_period, min_periods,
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"adx={self._adx_period}, atr={self._atr_period}, "
            f"rsi={self._rsi_period}, bb={self._bb_period}, "
            f"vol={self._volume_period}, min_periods={self._min_periods}"
            f")"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame) -> MarketRegime:
        """Detect the market regime from OHLCV data."""
        if not self._validate_input(df):
            logger.warning(
                "Invalid input type: %s — returning UNKNOWN.",
                type(df).__name__,
            )
            return MarketRegime(
                regime="UNKNOWN",
                reasons=[f"Expected a pandas DataFrame, got {type(df).__name__}."],
                metrics={"invalid_input": 1.0},
            )

        logger.info("Market regime detection started (rows=%d).", len(df))

        try:
            result = self._detect_impl(df)
            logger.warning(
                "Regime detected: %s (confidence=%.1f%%).",
                result.regime, result.confidence,
            )
            return result
        except Exception as exc:
            logger.exception("Market regime detection failed: %s", exc)
            return MarketRegime(
                regime="UNKNOWN",
                reasons=[f"Detection error: {exc}"],
                metrics={"error": 1.0},
            )

    def detect_dict(self, df: pd.DataFrame) -> dict[str, Any]:
        """Convenience: detect then to_dict."""
        return self.detect(df).to_dict()

    def detect_batch(self, dataframes: list[pd.DataFrame]) -> list[MarketRegime]:
        """Detect regimes for multiple DataFrames."""
        if not isinstance(dataframes, list):
            raise TypeError(
                f"Expected a list of DataFrames, got {type(dataframes).__name__}."
            )

        logger.info(
            "Batch regime detection started for %d DataFrames.",
            len(dataframes),
        )
        results: list[MarketRegime] = []
        for idx, df in enumerate(dataframes):
            logger.debug("Batch item %d / %d.", idx + 1, len(dataframes))
            results.append(self.detect(df))

        logger.warning("Batch regime detection completed: %d results.", len(results))
        return results

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_input(df: Any) -> bool:
        if df is None:
            return False
        if not isinstance(df, pd.DataFrame):
            return False
        return True

    # ------------------------------------------------------------------
    # Core detection — decision tree
    # ------------------------------------------------------------------

    def _detect_impl(self, df: pd.DataFrame) -> MarketRegime:
        # ---- Input validation ----
        if df.empty:
            logger.warning("Empty DataFrame received — returning UNKNOWN.")
            return MarketRegime(
                regime="UNKNOWN",
                reasons=["Empty DataFrame."],
                metrics={"empty": 1.0},
            )

        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        missing = required_cols - set(df.columns)
        if missing:
            logger.warning("Missing columns %s — returning UNKNOWN.", missing)
            return MarketRegime(
                regime="UNKNOWN",
                reasons=[f"Missing columns: {', '.join(sorted(missing))}."],
                metrics={"missing_columns": 1.0},
            )

        if len(df) < self._min_periods:
            logger.warning(
                "Insufficient history: %d rows (need %d) — returning UNKNOWN.",
                len(df), self._min_periods,
            )
            return MarketRegime(
                regime="UNKNOWN",
                reasons=[f"Insufficient history: {len(df)} rows (need {self._min_periods})."],
                metrics={"insufficient_history": float(len(df))},
            )

        # ---- Compute indicators ----
        df = self._compute_indicators(df)
        latest = df.iloc[-1]
        metrics: dict[str, float] = {}

        # -- Extract values --
        adx_val: float = self._safe_float(latest, "ADX")
        di_plus: float = self._safe_float(latest, "DI_PLUS")
        di_minus: float = self._safe_float(latest, "DI_MINUS")
        close_val: float = self._safe_float(latest, "Close", 0.0)
        atr_val: float = self._safe_float(latest, "ATR")
        atr_pct: float = (atr_val / close_val) if close_val > 0 else 0.0

        atr_series = df["ATR"].dropna()
        atr_percentile: float = (
            float(atr_series.rank(pct=True).iloc[-1])
            if len(atr_series) > 1
            else 0.5
        )
        sma20: float = self._safe_float(latest, "SMA_20")
        sma50: float = self._safe_float(latest, "SMA_50")

        # MA slope
        if "SMA_20" in df.columns and len(df) >= 6:
            sma20_series = df["SMA_20"].dropna()
            if len(sma20_series) >= 6:
                p6 = sma20_series.iloc[-6]
                ma_slope: float = (sma20 - p6) / p6 if p6 > 0 else 0.0
            else:
                ma_slope = 0.0
        else:
            ma_slope = 0.0

        price_position: float = (close_val - sma20) / sma20 if sma20 > 0 else 0.0
        rsi_val: float = self._safe_float(latest, "RSI", 50.0)
        macd_val: float = self._safe_float(latest, "MACD", 0.0)
        macd_signal: float = self._safe_float(latest, "MACD_SIGNAL", 0.0)
        macd_hist: float = macd_val - macd_signal
        volume_ratio: float = self._safe_float(latest, "RELATIVE_VOLUME", 1.0)
        obv_slope: float = self._compute_obv_slope(df)
        bb_width: float = self._safe_float(latest, "BB_WIDTH", 0.0)
        bb_position: float = self._safe_float(latest, "BB_POSITION", 0.5)

        # Returns
        def _ret(offset: int) -> float:
            if len(df) > abs(offset):
                prev = self._safe_float(df.iloc[offset], "Close", close_val)
                return (close_val / prev - 1.0) if prev > 0 else 0.0
            return 0.0

        returns_1: float = _ret(-2)
        returns_5: float = _ret(-6)
        returns_20: float = _ret(-21)

        # 20-day volatility
        cs = df["Close"].dropna()
        lr = cs.pct_change().dropna() if len(cs) > 1 else pd.Series(dtype=float)
        vol_20: float = float(lr.tail(20).std()) if len(lr) >= 20 else 0.0

        # Volume slope (from smoothed VOLUME_MA) — distinguishes genuine
        # accumulation/distribution from random OBV drift on sideways data.
        vma_series = df["VOLUME_MA"].dropna()
        if len(vma_series) >= 21:
            vma_seg = vma_series.tail(21)
            vma_sv = vma_seg.iloc[0]
            vma_ev = vma_seg.iloc[-1]
            volume_slope: float = (
                (vma_ev - vma_sv) / vma_sv if vma_sv > 0 else 0.0
            )
        else:
            volume_slope = 0.0

        # ---- Populate metrics ----
        metrics["adx"] = adx_val
        metrics["di_plus"] = di_plus
        metrics["di_minus"] = di_minus
        metrics["atr"] = atr_val
        metrics["atr_pct"] = atr_pct
        metrics["atr_percentile"] = atr_percentile
        metrics["sma20"] = sma20
        metrics["sma50"] = sma50
        metrics["ma_slope"] = ma_slope
        metrics["price_position"] = price_position
        metrics["rsi"] = rsi_val
        metrics["macd"] = macd_val
        metrics["macd_signal"] = macd_signal
        metrics["macd_histogram"] = macd_hist
        metrics["volume_ratio"] = volume_ratio
        metrics["obv_slope"] = obv_slope
        metrics["bb_width"] = bb_width
        metrics["bb_position"] = bb_position
        metrics["return_1d"] = returns_1
        metrics["return_5d"] = returns_5
        metrics["return_20d"] = returns_20
        metrics["volatility_20d"] = vol_20
        metrics["trend"] = ma_slope

        # Drawdown from 252-day rolling high
        rolling_max = df["Close"].rolling(window=252, min_periods=20).max()
        current_max = rolling_max.iloc[-1]
        metrics["drawdown"] = (current_max - close_val) / current_max if current_max > 0 else 0.0

        # Volume spike — compare to overall average for robust spike detection
        # during extended crashes where 20-period MA inflates
        vol_series = df["Volume"].dropna()
        if len(vol_series) < 2:
            metrics["volume_spike"] = 0.0
        else:
            overall_avg_vol = vol_series.mean()
            metrics["volume_spike"] = (
                1.0 if (overall_avg_vol > 0 and vol_series.iloc[-1] >= overall_avg_vol * 1.5)
                else 0.0
            )

        # ==============================================================
        # Decision tree — first matching regime wins
        # Priority order: PANIC > OVERHEATED > BULL > BEAR >
        # RECOVERY > DISTRIBUTION > ACCUMULATION > SIDEWAYS >
        # LOW_VOLATILITY > HIGH_VOLATILITY
        # ==============================================================

        regime: str = "UNKNOWN"
        reasons: list[str] = []
        confidence: float = 0.0

        # --- 1. PANIC ---
        if (
            metrics["trend"] < -0.05
            and metrics["drawdown"] > 0.15
            and metrics["volume_spike"]
        ):
            regime = "PANIC"
            reasons = [
                "Sharp market decline",
                "Large drawdown",
                "Volume spike confirms panic selling",
            ]
            confidence = min(abs(metrics["trend"]) * 1000.0, 100.0)

        # --- 2. OVERHEATED ---
        elif rsi_val > 65 and price_position > 0.02:
            regime = "OVERHEATED"
            reasons = [
                f"RSI {rsi_val:.1f} overbought",
                f"Price {price_position * 100:.1f}% above SMA20",
            ]
            if returns_5 > 0.05:
                reasons.append(f"Explosive 5-day move: {returns_5 * 100:.1f}%")
            confidence = min((rsi_val - 50.0) * 3.0, 100.0)

        # --- 3. BULL (strong uptrend) ---
        elif close_val > sma20 and sma20 > sma50:
            regime = "BULL"
            reasons = [
                f"Price above SMA20 ({price_position * 100:.1f}%)",
                "SMA20 above SMA50 (golden cross)",
            ]
            if adx_val > 20:
                reasons.append(f"ADX {adx_val:.1f} indicates trend")
            if rsi_val > 50:
                reasons.append(f"RSI {rsi_val:.1f} above 50")
            confidence = min(
                50.0 + price_position * 500.0 + max(0.0, adx_val - 20.0) * 2.0,
                100.0,
            )

        # --- 4. BEAR (strong downtrend) ---
        elif close_val < sma20 and sma20 < sma50:
            regime = "BEAR"
            reasons = [
                f"Price below SMA20 ({abs(price_position) * 100:.1f}%)",
                "SMA20 below SMA50 (death cross)",
            ]
            if adx_val > 20:
                reasons.append(f"ADX {adx_val:.1f} indicates trend")
            if rsi_val < 50:
                reasons.append(f"RSI {rsi_val:.1f} below 50")
            confidence = min(
                50.0 + abs(price_position) * 500.0 + max(0.0, adx_val - 20.0) * 2.0,
                100.0,
            )

        # --- 5. RECOVERY (bullish reversal after decline) ---
        elif (
            metrics["trend"] > 0.02
            and metrics["drawdown"] > 0.10
            and not metrics["volume_spike"]
        ):
            regime = "RECOVERY"
            reasons = [
                "Trend turning positive after decline",
                "Recovering from drawdown",
                "No panic selling",
            ]
            confidence = min(metrics["trend"] * 2000.0, 100.0)

        # --- 6. DISTRIBUTION (heavy selling after an uptrend) ---
        elif (
            metrics["trend"] > 0.01
            and metrics["drawdown"] < 0.10
            and metrics["volume_ratio"] >= 1.30
            and rsi_val >= 55
            and price_position < 0
        ):
            regime = "DISTRIBUTION"
            reasons = [
                "Heavy volume while price weakens after an uptrend.",
            ]
            confidence = 60.0

        # --- 7. ACCUMULATION (flat price, rising OBV, rising volume, not in bull trend) ---
        elif (
            abs(returns_20) < 0.05
            and obv_slope > 0.02
            and volume_slope > 0.01  # volume trend confirms accumulation
            and not (close_val > sma20 and sma20 > sma50)  # not a clear bull
        ):
            regime = "ACCUMULATION"
            reasons = [
                "Price relatively flat over 20 days",
                "OBV rising — accumulation detected",
            ]
            if volume_ratio > 1.2:
                reasons.append(f"Volume {volume_ratio:.1f}x average")
            confidence = min(obv_slope * 2000.0 + volume_ratio * 20.0, 100.0)

        # --- 8. SIDEWAYS (weak ADX, flat MAs, low volume) ---
        elif (
            abs(ma_slope) < 0.01
            and adx_val < 25
            and volume_ratio < 1.30
        ):
            regime = "SIDEWAYS"
            reasons = []
            if adx_val < 20:
                reasons.append(f"Low ADX {adx_val:.1f} — no strong trend")
            else:
                reasons.append(f"Moderate ADX {adx_val:.1f} — weak trend")
            if abs(ma_slope) < 0.002:
                reasons.append("Moving average flat")
            if atr_percentile < 0.3:
                reasons.append("ATR in low percentile (tight range)")
            if volume_ratio < 1.0:
                reasons.append("Below-average volume")
            confidence = max(0.0, min((25.0 - adx_val) * 5.0 + 30.0, 100.0))

        # --- 9. LOW_VOLATILITY (compressed price action) ---
        elif (
            (atr_percentile < 0.4 or atr_pct < 0.015)
            and bb_width < 0.05
            and abs(price_position) < 0.05
            and adx_val < 20
        ):
            regime = "LOW_VOLATILITY"
            reasons = [
                f"ATR at {atr_percentile:.0%} percentile (compressed)",
                "Narrow Bollinger Bands",
            ]
            if vol_20 < 0.01:
                reasons.append("Low 20-day volatility")
            confidence = min((1.0 - atr_percentile) * 100.0, 100.0)

        # --- 10. HIGH_VOLATILITY ---
        elif atr_percentile > 0.6 and bb_width > 0.07 and adx_val < 30:
            regime = "HIGH_VOLATILITY"
            reasons = [
                f"ATR at {atr_percentile:.0%} percentile",
                f"Bollinger width {bb_width:.3f}",
            ]
            if vol_20 > 0.02:
                reasons.append("High 20-day volatility")
            confidence = min(atr_percentile * 100.0, 100.0)

        # Fallback
        else:
            regime = "SIDEWAYS"
            reasons = ["No strong directional signal — classified as sideways"]
            confidence = 30.0

        trend_strength = min(adx_val, 100.0)
        volume_strength = self._compute_volume_strength(obv_slope, volume_ratio)

        return MarketRegime(
            regime=regime,
            confidence=round(confidence, 2),
            trend_strength=round(trend_strength, 4),
            volatility=round(atr_pct, 4),
            adx=round(adx_val, 2),
            atr=round(atr_pct, 6),
            moving_average_slope=round(ma_slope, 6),
            price_position=round(price_position, 6),
            volume_strength=round(volume_strength, 2),
            reasons=reasons,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Legacy scoring methods (kept for test unit tests)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_bull(
        close_val: float = 0.0,
        sma20: float = 0.0,
        sma50: float = 0.0,
        adx_val: float = 0.0,
        di_plus: float = 0.0,
        di_minus: float = 0.0,
        ma_slope: float = 0.0,
        rsi_val: float = 50.0,
        macd_val: float = 0.0,
        macd_hist: float = 0.0,
        returns_5: float = 0.0,
        returns_20: float = 0.0,
        sma20_series: pd.Series | None = None,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if sma20 > 0 and close_val > sma20:
            score += 1.5
            r.append("Price above SMA20")
        if sma50 > 0 and close_val > sma50:
            score += 1.0
            r.append("Price above SMA50")
        if sma20 > 0 and sma50 > 0 and sma20 > sma50:
            score += 1.0
            r.append("SMA20 above SMA50 (golden cross)")
        if adx_val > 25:
            score += 1.0
            r.append(f"ADX {adx_val:.1f} indicates strong trend")
        elif adx_val > 20:
            score += 0.5
        if di_plus > di_minus:
            score += 0.5
            r.append("+DI above -DI (bullish directional)")
        if ma_slope > 0.001:
            score += 0.5
            r.append("Moving average rising")
        if rsi_val > 55:
            score += 0.5
        if rsi_val > 50:
            score += 0.5
            r.append(f"RSI {rsi_val:.1f} above 50")
        if macd_hist > 0:
            score += 0.5
            r.append("MACD bullish (histogram positive)")
        if macd_val > 0:
            score += 0.5
        if returns_5 > 0.01:
            score += 0.5
        if returns_20 > 0.03:
            score += 0.5
            r.append("Positive 20-day return")
        return score, r

    @staticmethod
    def _score_bear(
        close_val: float,
        sma20: float,
        sma50: float,
        adx_val: float,
        di_plus: float,
        di_minus: float,
        ma_slope: float,
        rsi_val: float,
        macd_val: float,
        macd_hist: float,
        returns_5: float,
        returns_20: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if sma20 > 0 and close_val < sma20:
            score += 1.5
            r.append("Price below SMA20")
        if sma50 > 0 and close_val < sma50:
            score += 1.0
            r.append("Price below SMA50")
        if sma20 > 0 and sma50 > 0 and sma20 < sma50:
            score += 1.0
            r.append("SMA20 below SMA50 (death cross)")
        if adx_val > 25:
            score += 1.0
            r.append(f"ADX {adx_val:.1f} indicates strong trend")
        elif adx_val > 20:
            score += 0.5
        if di_minus > di_plus:
            score += 0.5
            r.append("-DI above +DI (bearish directional)")
        if ma_slope < -0.001:
            score += 0.5
            r.append("Moving average falling")
        if rsi_val < 45:
            score += 0.5
        if rsi_val < 50:
            score += 0.5
            r.append(f"RSI {rsi_val:.1f} below 50")
        if macd_hist < 0:
            score += 0.5
            r.append("MACD bearish (histogram negative)")
        if macd_val < 0:
            score += 0.5
        if returns_5 < -0.01:
            score += 0.5
        if returns_20 < -0.03:
            score += 0.5
            r.append("Negative 20-day return")
        return score, r

    @staticmethod
    def _score_sideways(
        adx_val: float,
        rsi_val: float,
        atr_percentile: float,
        ma_slope: float,
        bb_width: float,
        vol_20: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if adx_val < 20:
            score += 2.5
            r.append(f"Low ADX {adx_val:.1f} — no strong trend")
        elif adx_val < 25:
            score += 1.5
            r.append(f"Moderate ADX {adx_val:.1f} — weak trend")
        if 40 <= rsi_val <= 60:
            score += 2.0
            r.append(f"RSI {rsi_val:.1f} in neutral range")
        if abs(ma_slope) < 0.002:
            score += 1.5
            r.append("Moving average flat")
        if atr_percentile < 0.3:
            score += 1.0
            r.append("ATR in low percentile (tight range)")
        if bb_width < 0.05:
            score += 1.0
            r.append("Narrow Bollinger Bands")
        if vol_20 < 0.015:
            score += 0.5
        return score, r

    @staticmethod
    def _score_high_volatility(
        atr_percentile: float,
        bb_width: float,
        vol_20: float,
        atr_pct: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if atr_percentile > 0.7:
            score += 2.0
            r.append(f"ATR at {atr_percentile:.0%} percentile (elevated)")
        if bb_width > 0.10:
            score += 1.5
            r.append("Wide Bollinger Bands")
        if vol_20 > 0.02:
            score += 1.0
            r.append("High 20-day volatility")
        if atr_pct > 0.03:
            score += 0.5
        return score, r

    @staticmethod
    def _score_low_volatility(
        atr_percentile: float,
        bb_width: float,
        vol_20: float,
        atr_pct: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if atr_percentile < 0.3:
            score += 2.0
            r.append(f"ATR at {atr_percentile:.0%} percentile (compressed)")
        if bb_width < 0.04:
            score += 1.5
            r.append("Narrow Bollinger Bands")
        if vol_20 < 0.01:
            score += 1.0
            r.append("Low 20-day volatility")
        if atr_pct < 0.01:
            score += 0.5
        return score, r

    @staticmethod
    def _score_accumulation(
        returns_20: float,
        obv_slope: float,
        volume_ratio: float,
        sma20: float,
        close_val: float,
        bb_position: float,
        sma50: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if abs(returns_20) < 0.05:
            score += 1.5
            r.append("Price relatively flat over 20 days")
        if obv_slope > 0.01:
            score += 2.5
            r.append("OBV rising — accumulation detected")
        elif obv_slope > 0.005:
            score += 1.0
            r.append("OBV modestly rising")
        if volume_ratio > 1.2:
            score += 1.5
            r.append(f"Volume {volume_ratio:.1f}x average")
        elif volume_ratio > 1.0:
            score += 0.5
        if sma20 > 0 and abs(close_val - sma20) / sma20 < 0.02:
            score += 0.5
        if bb_position < 0.4:
            score += 1.0
            r.append("Price in lower band zone")
        if sma50 > 0 and close_val > sma50:
            score += 0.5
            r.append("Price above SMA50 (support intact)")
        if abs(returns_20) < 0.03:
            score += 0.5
        return score, r

    @staticmethod
    def _score_distribution(
        returns_20: float,
        obv_slope: float,
        volume_ratio: float,
        sma20: float,
        close_val: float,
        bb_position: float,
        sma50: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if abs(returns_20) < 0.05:
            score += 1.5
            r.append("Price relatively flat over 20 days")
        if obv_slope < -0.01:
            score += 2.5
            r.append("OBV falling — distribution detected")
        elif obv_slope < -0.005:
            score += 1.0
            r.append("OBV modestly falling")
        if volume_ratio > 1.3:
            score += 1.5
            r.append(f"Elevated volume {volume_ratio:.1f}x average")
        elif volume_ratio > 1.0:
            score += 0.5
        if sma20 > 0 and abs(close_val - sma20) / sma20 < 0.02:
            score += 0.5
        if bb_position > 0.6:
            score += 1.0
            r.append("Price in upper band zone")
        if sma50 > 0 and close_val < sma50:
            score += 1.0
            r.append("Price below SMA50 (distribution pattern)")
        if abs(returns_20) < 0.03:
            score += 0.5
        return score, r

    @staticmethod
    def _score_recovery(
        returns_5: float,
        returns_20: float,
        ma_slope: float,
        macd_hist: float,
        rsi_val: float,
        adx_val: float,
        di_plus: float,
        di_minus: float,
        close_val: float,
        sma20: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if returns_20 < -0.03 and returns_5 > 0.0:
            score += 2.0
            r.append("Recovering from recent decline")
        if ma_slope > 0.0 and returns_5 > returns_20:
            score += 1.0
            r.append("Momentum turning positive")
        if macd_hist > 0 and returns_5 > 0:
            score += 1.0
            r.append("MACD histogram positive (recovery signal)")
        if 30 <= rsi_val <= 50:
            score += 1.0
            r.append(f"RSI {rsi_val:.1f} climbing from oversold")
        if di_plus > di_minus:
            score += 0.5
            r.append("+DI crossing above -DI")
        if sma20 > 0 and close_val < sma20 and (sma20 - close_val) / sma20 < 0.03:
            score += 0.5
            r.append("Price approaching SMA20 from below")
        return score, r

    @staticmethod
    def _score_panic(
        returns_1: float,
        returns_5: float,
        volume_ratio: float,
        atr_percentile: float,
        rsi_val: float,
        vol_20: float,
        close_val: float,
        sma20: float,
        volume_spike: bool = False,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if returns_1 < -0.03:
            score += 1.5
            r.append(f"Sharp daily decline: {returns_1*100:.1f}%")
        elif returns_1 < -0.02:
            score += 1.0
        if returns_5 < -0.08:
            score += 1.5
            r.append(f"Sharp 5-day decline: {returns_5*100:.1f}%")
        elif returns_5 < -0.05:
            score += 1.0
            r.append(f"Sharp 5-day decline: {returns_5*100:.1f}%")
        if volume_spike:
            score += 2.0
            r.append("Volume spike detected")
        elif volume_ratio > 2.0:
            score += 1.5
            r.append(f"Volume spike: {volume_ratio:.1f}x average")
        elif volume_ratio > 1.5:
            score += 1.0
        if atr_percentile > 0.85:
            score += 1.5
            r.append("ATR at extreme percentile (panic)")
        elif atr_percentile > 0.7:
            score += 0.5
        if rsi_val < 25:
            score += 1.5
            r.append(f"RSI {rsi_val:.1f} deeply oversold")
        elif rsi_val < 35:
            score += 1.0
        if vol_20 > 0.03:
            score += 0.5
            r.append("Elevated 20-day volatility")
        if sma20 > 0 and close_val < sma20 and (sma20 - close_val) / sma20 > 0.05:
            score += 0.5
            r.append("Price far below SMA20")
        return score, r

    @staticmethod
    def _score_overheated(
        rsi_val: float,
        price_position: float,
        returns_5: float,
        returns_20: float,
        ma_slope: float,
        volume_ratio: float,
        macd_hist: float,
        bb_position: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        r: list[str] = []
        if rsi_val > 75:
            score += 2.0
            r.append(f"RSI {rsi_val:.1f} deeply overbought")
        elif rsi_val > 65:
            score += 1.0
        if price_position > 0.05:
            score += 1.5
            r.append(f"Price {price_position*100:.1f}% above SMA20 (extended)")
        elif price_position > 0.03:
            score += 1.0
        if returns_5 > 0.07:
            score += 1.0
            r.append(f"Explosive 5-day move: {returns_5*100:.1f}%")
        if returns_20 > 0.15:
            score += 0.5
        if ma_slope > 0.01:
            score += 0.5
            r.append("Moving average steeply rising")
        if volume_ratio > 1.5:
            score += 0.5
        if macd_hist < 0 and returns_20 > 0.05:
            score += 1.0
            r.append("Bearish MACD divergence (price up, histogram down)")
        if bb_position > 0.9:
            score += 0.5
            r.append("Price near upper Bollinger Band")
        return score, r

    # ------------------------------------------------------------------
    # Indicator computation
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        df["SMA_20"] = close.rolling(window=20).mean()
        df["SMA_50"] = close.rolling(window=50).mean()
        df["SMA_200"] = close.rolling(window=200).mean()

        # ATR
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        df["ATR"] = tr.rolling(window=self._atr_period).mean()

        # ADX
        adx_df = self._compute_adx(high=high, low=low, close=close, period=self._adx_period)
        df["ADX"] = adx_df["ADX"]
        df["DI_PLUS"] = adx_df["DI_PLUS"]
        df["DI_MINUS"] = adx_df["DI_MINUS"]

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        ag = gain.rolling(window=self._rsi_period).mean()
        al = loss.rolling(window=self._rsi_period).mean()
        rs = ag / al.replace(0, float("nan"))
        df["RSI"] = 100.0 - (100.0 / (1.0 + rs))

        # MACD
        e12 = close.ewm(span=12, adjust=False).mean()
        e26 = close.ewm(span=26, adjust=False).mean()
        df["MACD"] = e12 - e26
        df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()

        # Bollinger Bands
        bm = close.rolling(window=self._bb_period).mean()
        bs = close.rolling(window=self._bb_period).std()
        df["BB_UPPER"] = bm + 2.0 * bs
        df["BB_LOWER"] = bm - 2.0 * bs
        df["BB_WIDTH"] = (df["BB_UPPER"] - df["BB_LOWER"]) / bm.replace(0, float("nan"))
        df["BB_POSITION"] = (close - df["BB_LOWER"]) / (
            df["BB_UPPER"] - df["BB_LOWER"]
        ).replace(0, float("nan"))

        # Volume
        vm = volume.rolling(window=self._volume_period).mean()
        df["VOLUME_MA"] = vm
        df["RELATIVE_VOLUME"] = volume / vm.replace(0, float("nan"))

        # OBV
        df["OBV"] = self._compute_obv(close=close, volume=volume)
        return df

    @staticmethod
    def _compute_adx(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
    ) -> pd.DataFrame:
        df = pd.DataFrame(index=high.index)
        pc = close.shift(1)
        ph = high.shift(1)
        pl = low.shift(1)

        tr = pd.concat(
            [high - low, (high - pc).abs(), (low - pc).abs()], axis=1,
        ).max(axis=1)

        up = high - ph
        down = pl - low
        pdm = pd.Series(0.0, index=high.index)
        mdm = pd.Series(0.0, index=high.index)
        pdm[(up > down) & (up > 0)] = up[(up > down) & (up > 0)]
        mdm[(down > up) & (down > 0)] = down[(down > up) & (down > 0)]

        def ws(s: pd.Series, p: int) -> pd.Series:
            out = s.copy()
            fi = s.first_valid_index()
            if fi is None:
                return out
            idx = s.index.get_loc(fi)
            if idx + p - 1 < len(s):
                out.iloc[idx + p - 1] = s.iloc[idx: idx + p].mean()
                for i in range(idx + p, len(s)):
                    out.iloc[i] = out.iloc[i - 1] + (s.iloc[i] - out.iloc[i - 1]) / p
            return out

        trs = ws(tr, period)
        pds = ws(pdm, period)
        mds = ws(mdm, period)

        dip = 100.0 * pds / trs.replace(0, float("nan"))
        dim = 100.0 * mds / trs.replace(0, float("nan"))
        dx = 100.0 * (dip - dim).abs() / (dip + dim).replace(0, float("nan"))
        adx = ws(dx, period)

        df["DI_PLUS"] = dip
        df["DI_MINUS"] = dim
        df["ADX"] = adx
        return df

    @staticmethod
    def _compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        obv = pd.Series(0.0, index=close.index)
        diff = close.diff()
        for i in range(1, len(close)):
            if diff.iloc[i] > 0:
                obv.iloc[i] = obv.iloc[i - 1] + volume.iloc[i]
            elif diff.iloc[i] < 0:
                obv.iloc[i] = obv.iloc[i - 1] - volume.iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i - 1]
        return obv

    @staticmethod
    def _compute_obv_slope(df: pd.DataFrame, window: int = 10) -> float:
        if "OBV" not in df.columns:
            return 0.0
        s = df["OBV"].dropna()
        if len(s) < window + 1:
            return 0.0
        seg = s.tail(window + 1)
        sv = seg.iloc[0]
        ev = seg.iloc[-1]
        if sv == 0:
            return 0.0
        return (ev - sv) / abs(sv)

    @staticmethod
    def _compute_volume_strength(obv_slope: float, volume_ratio: float) -> float:
        score = 50.0
        if obv_slope > 0.02:
            score += 20.0
        elif obv_slope > 0.01:
            score += 10.0
        elif obv_slope < -0.02:
            score -= 20.0
        elif obv_slope < -0.01:
            score -= 10.0
        if volume_ratio > 2.0:
            score += 15.0
        elif volume_ratio > 1.5:
            score += 10.0
        elif volume_ratio > 1.2:
            score += 5.0
        elif volume_ratio < 0.5:
            score -= 10.0
        elif volume_ratio < 0.7:
            score -= 5.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _detect_volume_spike(df: pd.DataFrame, multiplier: float = 1.5) -> bool:
        """Detect a volume spike by comparing the latest volume to the
        baseline — the max of the first entry and the rolling mean of the
        early portion of the series.
        """
        volume = df["Volume"].dropna()
        if len(volume) < _VOLUME_PERIOD + 1:
            return False
        baseline = max(volume.iloc[0], volume.head(5).mean())
        current_volume = volume.iloc[-1]
        if baseline <= 0:
            return False
        return current_volume > baseline * multiplier

    @staticmethod
    def _safe_float(series: pd.Series, key: str, default: float = 0.0) -> float:
        try:
            val = series.get(key, default)
            if pd.isna(val):
                return default
            return float(val)
        except (ValueError, TypeError, KeyError):
            return default
