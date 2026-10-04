"""Multi-timeframe engine (Part 9.8).

Synchronises daily, weekly, and monthly frames so a strategy can use
higher-timeframe context (e.g. weekly trend filter) while trading on
the daily bars.  Each master daily bar exposes the latest weekly and
monthly snapshots aligned to that date.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.backtesting.timeline import build_multi_timeframe, resample_frame

logger = logging.getLogger(__name__)


@dataclass
class AlignedBars:
    """A daily bar plus its aligned higher-timeframe context.

    Attributes:
        timestamp: The daily bar date.
        daily: The daily bar row (Series or dict).
        weekly: The weekly bar active on this date (or None).
        monthly: The monthly bar active on this date (or None).
    """

    timestamp: Any
    daily: Any
    weekly: Any = None
    monthly: Any = None


class MultiTimeframeEngine:
    """Provides aligned daily + weekly + monthly views for a symbol.

    Usage::

        mtf = MultiTimeframeEngine(df)
        for aligned in mtf.iter_aligned():
            # aligned.weekly["Close"] is the current weekly close
            pass
    """

    def __init__(
        self,
        df: pd.DataFrame,
        date_column: str = "Date",
    ) -> None:
        """Initialise the engine from a daily frame.

        Args:
            df: Daily OHLCV DataFrame.
            date_column: Date column name.
        """
        self._date_column = date_column
        self._frames = build_multi_timeframe(df, date_column)
        self._daily = self._frames["1D"]
        self._weekly = self._frames["1W"]
        self._monthly = self._frames["1M"]
        self._aligned: list[AlignedBars] = []
        self._build_aligned()

    def _build_aligned(self) -> None:
        """Precompute daily rows with aligned weekly/monthly context."""
        daily = self._daily.copy()
        if self._date_column not in daily.columns:
            return
        daily[self._date_column] = pd.to_datetime(daily[self._date_column])

        weekly = self._weekly.copy()
        monthly = self._monthly.copy()
        if self._date_column in weekly.columns:
            weekly[self._date_column] = pd.to_datetime(weekly[self._date_column])
        if self._date_column in monthly.columns:
            monthly[self._date_column] = pd.to_datetime(monthly[self._date_column])

        w_idx = 0
        m_idx = 0
        for _, row in daily.iterrows():
            dt = row[self._date_column]
            # Advance weekly pointer while the week bar is still before dt.
            while (
                w_idx + 1 < len(weekly)
                and weekly[self._date_column].iloc[w_idx + 1] <= dt
            ):
                w_idx += 1
            while (
                m_idx + 1 < len(monthly)
                and monthly[self._date_column].iloc[m_idx + 1] <= dt
            ):
                m_idx += 1

            week_row = weekly.iloc[w_idx].to_dict() if len(weekly) else None
            month_row = monthly.iloc[m_idx].to_dict() if len(monthly) else None
            self._aligned.append(
                AlignedBars(
                    timestamp=dt,
                    daily=row.to_dict(),
                    weekly=week_row,
                    monthly=month_row,
                )
            )

    def iter_aligned(self) -> Any:
        """Yield :class:`AlignedBars` per daily bar."""
        return iter(self._aligned)

    def frames(self) -> dict[str, pd.DataFrame]:
        """Return the daily/weekly/monthly frames."""
        return dict(self._frames)

    def latest_weekly(self, dt: Any) -> dict[str, Any] | None:
        """Return the weekly bar active at date *dt* (or None)."""
        target = pd.Timestamp(dt)
        best = None
        for aligned in self._aligned:
            if aligned.timestamp > target:
                break
            if aligned.weekly is not None:
                best = aligned.weekly
        return best

    def latest_monthly(self, dt: Any) -> dict[str, Any] | None:
        """Return the monthly bar active at date *dt* (or None)."""
        target = pd.Timestamp(dt)
        best = None
        for aligned in self._aligned:
            if aligned.timestamp > target:
                break
            if aligned.monthly is not None:
                best = aligned.monthly
        return best

    def resample(self, rule: str) -> pd.DataFrame:
        """Resample the daily frame to an arbitrary pandas rule.

        Args:
            rule: Pandas offset alias (e.g. ``"W"``, ``"ME"``, ``"2W"``).

        Returns:
            The resampled DataFrame.
        """
        return resample_frame(self._daily, rule, self._date_column)

    def __len__(self) -> int:
        """Return the number of daily bars."""
        return len(self._daily)
