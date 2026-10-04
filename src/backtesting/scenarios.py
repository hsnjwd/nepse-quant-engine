"""Scenario laboratory for the institutional backtesting engine.

Part 9.9 — stress-test strategies under synthetic market conditions
(bull, bear, sideways, high/low volatility, flash crash, liquidity
crisis, randomised).  Transforms OHLCV DataFrames and lets users
compare results across scenarios.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Scenario names.
BULL = "bull"
BEAR = "bear"
SIDEWAYS = "sideways"
HIGH_VOL = "high_volatility"
LOW_VOL = "low_volatility"
FLASH_CRASH = "flash_crash"
LIQUIDITY_CRISIS = "liquidity_crisis"
RANDOMIZED = "randomized"

SCENARIOS: tuple[str, ...] = (
    BULL,
    BEAR,
    SIDEWAYS,
    HIGH_VOL,
    LOW_VOL,
    FLASH_CRASH,
    LIQUIDITY_CRISIS,
    RANDOMIZED,
)


@dataclass
class ScenarioResult:
    """Comparison output for one scenario.

    Attributes:
        scenario: Scenario name.
        total_return: Total fractional return.
        max_drawdown: Maximum percentage drawdown (0–100).
        sharpe: Annualised Sharpe.
        final_equity: Ending equity.
        trades: Number of closed trades.
        data: Summary of the transformation applied.
    """

    scenario: str
    total_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    final_equity: float = 0.0
    trades: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "scenario": self.scenario,
            "total_return": round(self.total_return, 6),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 4),
            "final_equity": round(self.final_equity, 2),
            "trades": self.trades,
        }


def _scale_prices(df: pd.DataFrame, factor: float) -> pd.DataFrame:
    """Scale OHLC prices by a factor."""
    frame = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in frame.columns:
            frame[col] = frame[col].astype(float) * factor
    return frame


def _add_returns(
    df: pd.DataFrame,
    daily_return: float,
    noise: float = 0.01,
    seed: int | None = None,
) -> pd.DataFrame:
    """Simulate a trending series by compounding daily returns."""
    rng = random.Random(seed)
    frame = df.copy()
    closes = frame["Close"].astype(float).tolist()
    if not closes:
        return frame
    out = [closes[0]]
    for i in range(1, len(closes)):
        shock = rng.gauss(0, noise)
        out.append(out[-1] * (1 + daily_return + shock))
    frame["Close"] = out
    if "Open" in frame.columns:
        frame["Open"] = [out[0]] + out[:-1]
        frame["High"] = [max(o, c) * (1 + abs(rng.gauss(0, noise / 2))) for o, c in zip(frame["Open"], out)]
        frame["Low"] = [min(o, c) * (1 - abs(rng.gauss(0, noise / 2))) for o, c in zip(frame["Open"], out)]
    return frame


def _flash_crash(
    df: pd.DataFrame,
    crash_pct: float = 0.25,
    recovery_bars: int = 10,
    seed: int | None = None,
) -> pd.DataFrame:
    """Inject a sudden crash followed by recovery."""
    rng = random.Random(seed)
    frame = df.copy()
    if len(frame) < 2 or "Close" not in frame.columns:
        return frame
    start = max(1, len(frame) // 2)
    closes = frame["Close"].astype(float).tolist()
    for i in range(start, min(start + 2, len(closes))):
        closes[i] *= (1 - crash_pct)
    # Linear recovery over recovery_bars.
    trough = closes[start] if start < len(closes) else closes[-1]
    target = closes[min(start + recovery_bars, len(closes) - 1)] if len(closes) > start + 1 else trough
    for i in range(start + 2, min(start + recovery_bars, len(closes))):
        closes[i] = trough + (target - trough) * (i - start) / recovery_bars
    closes = closes[: len(frame)]
    # Ensure monotonic-ish final bar.
    if len(closes) > 1 and closes[-1] <= 0:
        closes[-1] = closes[-2] * 0.99
    frame["Close"] = closes
    return frame


def _liquidity_crisis(
    df: pd.DataFrame,
    volume_cut: float = 0.2,
    price_drop: float = 0.15,
    seed: int | None = None,
) -> pd.DataFrame:
    """Crush volume and drop prices mid-series to simulate a liquidity crisis."""
    rng = random.Random(seed)
    frame = df.copy()
    if len(frame) < 2:
        return frame
    start = max(1, len(frame) // 3)
    end = min(len(frame), start + len(frame) // 3)
    if "Volume" in frame.columns:
        vols = frame["Volume"].astype(float).tolist()
        for i in range(start, end):
            vols[i] *= (volume_cut + rng.uniform(0, 0.1))
        frame["Volume"] = vols
    if "Close" in frame.columns:
        closes = frame["Close"].astype(float).tolist()
        for i in range(start, end):
            closes[i] *= (1 - price_drop)
        frame["Close"] = closes
    return frame


def _sideways(df: pd.DataFrame, band: float = 0.03, seed: int | None = None) -> pd.DataFrame:
    """Constrain the series to a narrow trading band around the mean."""
    rng = random.Random(seed)
    frame = df.copy()
    closes = frame["Close"].astype(float).tolist()
    if not closes:
        return frame
    mean = sum(closes) / len(closes)
    out = [mean * (1 + rng.uniform(-band, band))]
    for _ in closes[1:]:
        out.append(out[-1] * (1 + rng.uniform(-band, band)))
    frame["Close"] = out
    return frame


def _volatility_adjust(df: pd.DataFrame, multiplier: float, seed: int | None = None) -> pd.DataFrame:
    """Multiply intraday range and returns by *multiplier*."""
    rng = random.Random(seed)
    frame = df.copy()
    if "Close" not in frame.columns:
        return frame
    closes = frame["Close"].astype(float).tolist()
    out = [closes[0]]
    for i in range(1, len(closes)):
        ret = closes[i] / closes[i - 1] - 1 if closes[i - 1] else 0.0
        ret *= multiplier
        out.append(out[-1] * (1 + ret))
    frame["Close"] = out
    return frame


_TRANSFORMERS: dict[str, Callable[..., pd.DataFrame]] = {
    BULL: lambda df, **kw: _add_returns(df, daily_return=0.0015, noise=0.008, seed=kw.get("seed")),
    BEAR: lambda df, **kw: _add_returns(df, daily_return=-0.0015, noise=0.01, seed=kw.get("seed")),
    SIDEWAYS: _sideways,
    HIGH_VOL: lambda df, **kw: _volatility_adjust(df, multiplier=3.0, seed=kw.get("seed")),
    LOW_VOL: lambda df, **kw: _volatility_adjust(df, multiplier=0.3, seed=kw.get("seed")),
    FLASH_CRASH: _flash_crash,
    LIQUIDITY_CRISIS: _liquidity_crisis,
    RANDOMIZED: lambda df, **kw: _add_returns(
        df,
        daily_return=random.Random(kw.get("seed")).uniform(-0.002, 0.002),
        noise=0.02,
        seed=kw.get("seed"),
    ),
}


class ScenarioLab:
    """Applies market scenarios to data and runs strategies through them.

    Usage::

        lab = ScenarioLab(engine_factory=my_engine_factory)
        results = lab.run_all(data, seed=42)
    """

    def __init__(self, engine_factory: Callable[..., Any] | None = None) -> None:
        """Initialise the lab.

        Args:
            engine_factory: Optional callable returning a configured
                :class:`BacktestEngine`.  Defaults to a plain engine.
        """
        self._engine_factory = engine_factory
        self._results: list[ScenarioResult] = []

    def transform(
        self,
        data: dict[str, pd.DataFrame],
        scenario: str,
        seed: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Apply *scenario* to all DataFrames.

        Args:
            data: Symbol -> OHLCV DataFrame mapping.
            scenario: Scenario name (see :data:`SCENARIOS`).
            seed: Optional RNG seed for reproducible scenarios.

        Returns:
            The transformed mapping.

        Raises:
            ValueError: If the scenario is unknown.
        """
        key = scenario.lower()
        if key not in _TRANSFORMERS:
            raise ValueError(
                f"Unknown scenario '{scenario}'. Available: {list(SCENARIOS)}"
            )
        transformer = _TRANSFORMERS[key]
        return {sym: transformer(df, seed=seed) for sym, df in data.items()}

    def run(
        self,
        data: dict[str, pd.DataFrame],
        scenario: str,
        seed: int | None = None,
    ) -> ScenarioResult:
        """Transform *data* with *scenario* and run a backtest.

        Args:
            data: Symbol -> OHLCV DataFrame mapping.
            scenario: Scenario name.
            seed: Optional RNG seed.

        Returns:
            A :class:`ScenarioResult`.
        """
        transformed = self.transform(data, scenario, seed)
        engine = self._engine_factory() if self._engine_factory else None
        if engine is None:
            from src.backtesting.engine import BacktestEngine
            engine = BacktestEngine()

        result = engine.run(transformed)
        metrics = result.metrics
        result_obj = ScenarioResult(
            scenario=scenario,
            total_return=metrics.total_return,
            max_drawdown=metrics.max_drawdown,
            sharpe=metrics.sharpe,
            final_equity=result.equity_curve[-1] if result.equity_curve else 0.0,
            trades=len(result.trades),
            data={"fills": len(result.fills), "bars": len(result.equity_curve)},
        )
        self._results.append(result_obj)
        return result_obj

    def run_all(
        self,
        data: dict[str, pd.DataFrame],
        seed: int | None = None,
        scenarios: list[str] | None = None,
    ) -> list[ScenarioResult]:
        """Run every scenario (or a subset) and return the results.

        Args:
            data: Symbol -> OHLCV DataFrame mapping.
            seed: Optional RNG seed.
            scenarios: Optional scenario subset; defaults to all.

        Returns:
            List of :class:`ScenarioResult` in scenario order.
        """
        self._results = []
        for scenario in scenarios or list(SCENARIOS):
            try:
                self.run(data, scenario, seed)
                logger.info("Scenario %s completed", scenario)
            except Exception as exc:
                logger.error("Scenario %s failed: %s", scenario, exc)
                self._results.append(ScenarioResult(
                    scenario=scenario, data={"error": str(exc)},
                ))
        return self._results

    def comparison_table(self) -> list[dict[str, Any]]:
        """Return the comparison table as JSON-ready dicts."""
        return [r.to_dict() for r in self._results]

    def results(self) -> list[ScenarioResult]:
        """Return recorded results."""
        return list(self._results)
