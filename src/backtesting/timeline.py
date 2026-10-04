"""Timeline management for the institutional backtesting engine.

Handles bar-by-bar iteration over one or more symbol DataFrames,
synchronises a master timeline, and provides multi-timeframe
aggregation (daily/weekly/monthly) for Part 9.8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.backtesting.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class TimelineSnapshot:
    """A single time step on the master timeline.

    Attributes:
        bar_index: Master index (0-based).
        timestamp: The timestamp for this step.
        bars: Mapping of symbol -> Bar for this step.
    """

    bar_index: int
    timestamp: Any
    bars: dict[str, Bar]

    def price(self, symbol: str, field: str = "close") -> float | None:
        """Return a price field for *symbol* (None when absent)."""
        bar = self.bars.get(symbol)
        if bar is None:
            return None
        return float(getattr(bar, field))


class BacktestTimeline:
    """Synchronised bar timeline across multiple symbols.

    Builds a master index from the union of all symbol timestamps.
    On each step, the bars for symbols that traded at that timestamp
    are made available; symbols with no bar for the step carry
    forward their previous close (available via :meth:`carried_price`).

    Usage::

        timeline = BacktestTimeline(data={"NABIL": df1, "ADBL": df2})
        for snapshot in timeline.iter_steps():
            ...  # snapshot.bars, snapshot.timestamp
    """

    def __init__(
        self,
        data: dict[str, pd.DataFrame],
        date_column: str = "Date",
    ) -> None:
        """Initialise the timeline from symbol DataFrames.

        Args:
            data: Mapping of symbol -> OHLCV DataFrame.
            date_column: Column used as the timestamp.

        Raises:
            ValueError: If no data is supplied.
        """
        if not data:
            raise ValueError("Timeline requires at least one symbol DataFrame.")
        self._date_column = date_column
        self._data: dict[str, pd.DataFrame] = {}
        self._master_index: list[Any] = []
        self._symbol_bars: dict[str, list[Bar]] = {}
        self._symbol_to_master: dict[str, dict[Any, int]] = {}
        self._build(data)

    # ── Construction ────────────────────────────────────────────

    def _build(self, data: dict[str, pd.DataFrame]) -> None:
        """Normalise DataFrames and compute the master timeline."""
        all_dates: set[Any] = set()
        for symbol, df in data.items():
            frame = df.copy()
            if self._date_column not in frame.columns:
                if isinstance(frame.index, pd.DatetimeIndex):
                    frame[self._date_column] = frame.index
                else:
                    frame[self._date_column] = range(len(frame))
            frame[self._date_column] = pd.to_datetime(frame[self._date_column])
            frame = frame.sort_values(self._date_column).reset_index(drop=True)
            self._data[symbol] = frame
            all_dates.update(frame[self._date_column].tolist())
            self._symbol_bars[symbol] = []
            self._symbol_to_master[symbol] = {}

        self._master_index = sorted(all_dates)

        # Pre-compute bar objects and master index mapping.
        for symbol, frame in self._data.items():
            dates = frame[self._date_column].tolist()
            master_pos: dict[Any, int] = {}
            for i, dt in enumerate(dates):
                try:
                    bar_idx = self._master_index.index(dt)
                except ValueError:
                    bar_idx = -1
                master_pos[dt] = bar_idx
                self._symbol_bars[symbol].append(
                    Bar(
                        symbol=symbol,
                        timestamp=dt,
                        open=float(frame["Open"].iloc[i]) if "Open" in frame.columns else 0.0,
                        high=float(frame["High"].iloc[i]) if "High" in frame.columns else 0.0,
                        low=float(frame["Low"].iloc[i]) if "Low" in frame.columns else 0.0,
                        close=float(frame["Close"].iloc[i]) if "Close" in frame.columns else 0.0,
                        volume=float(frame["Volume"].iloc[i]) if "Volume" in frame.columns else 0.0,
                        index=i,
                    )
                )
            self._symbol_to_master[symbol] = master_pos

        logger.debug("Timeline built: %d master steps for %d symbols", len(self._master_index), len(self._data))

    # ── Iteration ───────────────────────────────────────────────

    def iter_steps(self) -> Any:
        """Yield :class:`TimelineSnapshot` for each master step."""
        for bar_idx, ts in enumerate(self._master_index):
            bars: dict[str, Bar] = {}
            for symbol, pos_map in self._symbol_to_master.items():
                sym_idx = pos_map.get(ts)
                if sym_idx is not None and sym_idx >= 0:
                    bars[symbol] = self._symbol_bars[symbol][sym_idx]
            yield TimelineSnapshot(bar_index=bar_idx, timestamp=ts, bars=bars)

    # ── Accessors ───────────────────────────────────────────────

    @property
    def symbols(self) -> list[str]:
        """Return the list of traded symbols."""
        return list(self._data.keys())

    @property
    def master_length(self) -> int:
        """Return the number of master steps."""
        return len(self._master_index)

    @property
    def master_dates(self) -> list[Any]:
        """Return the master timestamps in order."""
        return list(self._master_index)

    def raw_frame(self, symbol: str) -> pd.DataFrame:
        """Return the normalised DataFrame for *symbol*."""
        return self._data[symbol]

    def previous_close(self, symbol: str, bar_index: int) -> float | None:
        """Return the most recent close for *symbol* at or before *bar_index*.

        Args:
            symbol: Ticker symbol.
            bar_index: Master timeline index.

        Returns:
            The last known close, or ``None`` if no bar exists yet.
        """
        pos_map = self._symbol_to_master.get(symbol, {})
        latest: float | None = None
        for dt, idx in pos_map.items():
            if idx <= bar_index:
                latest = float(self._bar_at(symbol, idx).close)
        return latest

    def _bar_at(self, symbol: str, master_idx: int) -> Bar:
        """Return the bar of *symbol* whose master index equals *master_idx*."""
        pos_map = self._symbol_to_master.get(symbol, {})
        for dt, idx in pos_map.items():
            if idx == master_idx:
                sym_idx = self._data[symbol][self._date_column].tolist().index(dt)
                return self._symbol_bars[symbol][sym_idx]
        raise KeyError(f"No bar for {symbol} at master index {master_idx}")


# ═══════════════════════════════════════════════════════════════════
# Multi-timeframe helpers (Part 9.8)
# ═══════════════════════════════════════════════════════════════════


def resample_frame(
    df: pd.DataFrame,
    rule: str,
    date_column: str = "Date",
) -> pd.DataFrame:
    """Resample a daily OHLCV DataFrame to another timeframe.

    Args:
        df: DataFrame with ``Date``, ``Open``, ``High``, ``Low``, ``Close``, ``Volume``.
        rule: Pandas offset alias (e.g. ``"W"`` weekly, ``"ME"`` month-end).
        date_column: The date column name.

    Returns:
        A resampled DataFrame indexed by the period end, or the input
        unchanged when resampling fails.
    """
    frame = df.copy()
    if date_column not in frame.columns:
        return frame
    frame[date_column] = pd.to_datetime(frame[date_column])
    frame = frame.set_index(date_column)
    try:
        agg = frame.resample(rule).agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        ).dropna(subset=["Open", "Close"])
        agg = agg.reset_index()
        return agg
    except Exception as exc:
        logger.warning("Resampling to %s failed: %s", rule, exc)
        return df


def build_multi_timeframe(
    df: pd.DataFrame,
    date_column: str = "Date",
) -> dict[str, pd.DataFrame]:
    """Build daily, weekly, and monthly frames from a daily frame.

    Args:
        df: Daily OHLCV DataFrame.
        date_column: Date column name.

    Returns:
        Mapping of ``"1D"``, ``"1W"``, ``"1M"`` to DataFrames.
    """
    return {
        "1D": df.copy(),
        "1W": resample_frame(df, "W", date_column),
        "1M": resample_frame(df, "ME", date_column),
    }
