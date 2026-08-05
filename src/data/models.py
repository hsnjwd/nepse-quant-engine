"""Data models for the NEPSE Quant Engine data service.

Every provider returns instances of these dataclasses — never raw dictionaries.
This ensures type safety and a consistent contract across all data sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

import pandas as pd


# ═══════════════════════════════════════════════════════════════════
# Market-level models
# ═══════════════════════════════════════════════════════════════════


@dataclass
class MarketStatus:
    """Trading session status for the NEPSE exchange."""

    status: str = "Unknown"  # "Open" | "Closed" | "Holiday" | "Unknown"
    is_open: bool = False
    last_updated: datetime = field(default_factory=datetime.now)


@dataclass
class MarketSummary:
    """Live snapshot of the NEPSE market index and breadth data."""

    index: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    turnover: float = 0.0
    advances: int = 0
    declines: int = 0
    unchanged: int = 0
    status: str = "Unknown"
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def empty(cls) -> MarketSummary:
        """Return a zeroed-out summary (safe default when data is unavailable)."""
        return cls()

    @property
    def is_market_open(self) -> bool:
        return self.status.lower() == "open"

    @property
    def advance_decline_ratio(self) -> float:
        if self.declines == 0:
            return float(self.advances) if self.advances > 0 else 1.0
        return self.advances / self.declines


# ═══════════════════════════════════════════════════════════════════
# Stock-level models
# ═══════════════════════════════════════════════════════════════════


@dataclass
class StockQuote:
    """Live quote for a single NEPSE stock at a point in time."""

    symbol: str = ""
    company_name: str = ""
    ltp: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    open_price: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    turnover: float = 0.0
    previous_close: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def empty(cls) -> StockQuote:
        """Return a zeroed-out quote (safe default)."""
        return cls()


@dataclass
class StockHistory:
    """OHLCV price history for a stock, returned as a DataFrame.

    The ``df`` attribute contains columns ``Date``, ``Open``, ``High``,
    ``Low``, ``Close``, ``Volume`` (and optionally ``Turnover``).
    """

    symbol: str = ""
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    days: int = 0
    source: str = ""  # "api" | "csv" | "cache"

    @property
    def is_empty(self) -> bool:
        return self.df.empty

    @property
    def latest_close(self) -> float:
        if not self.is_empty and "Close" in self.df.columns:
            return float(self.df["Close"].iloc[-1])
        return 0.0

    @property
    def date_range(self) -> tuple[date | None, date | None]:
        if not self.is_empty and "Date" in self.df.columns:
            dates = self.df["Date"]
            return (dates.iloc[0].date(), dates.iloc[-1].date())
        return (None, None)


# ═══════════════════════════════════════════════════════════════════
# Top movers
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TopMover:
    """A single entry in a top-gainers, top-losers, or top-turnover list."""

    symbol: str = ""
    ltp: float = 0.0
    change_pct: float = 0.0
    turnover: float = 0.0
    volume: int = 0


@dataclass
class TopMovers:
    """Collection of top gainers, losers, and turnover stocks."""

    gainers: list[TopMover] = field(default_factory=list)
    losers: list[TopMover] = field(default_factory=list)
    turnover: list[TopMover] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def empty(cls) -> TopMovers:
        """Return empty top movers (safe default)."""
        return cls()


# ═══════════════════════════════════════════════════════════════════
# Market scan
# ═══════════════════════════════════════════════════════════════════


@dataclass
class MarketScanResult:
    """Result of scanning the entire market (or a subset)."""

    results: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    total_scanned: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def buy_count(self) -> int:
        return sum(1 for r in self.results if r.get("signal", "").upper() in ("BUY", "STRONG_BUY"))

    @property
    def sell_count(self) -> int:
        return sum(1 for r in self.results if r.get("signal", "").upper() in ("SELL", "STRONG_SELL"))

    @property
    def hold_count(self) -> int:
        return sum(1 for r in self.results if r.get("signal", "").upper() == "HOLD")


# ═══════════════════════════════════════════════════════════════════
# Watchlist
# ═══════════════════════════════════════════════════════════════════


@dataclass
class WatchlistEntry:
    """A single stock in a watchlist with its latest analysis."""

    symbol: str = ""
    price: float = 0.0
    signal: str = "HOLD"
    score: float = 0.0
    confidence: float = 0.0
    rsi: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)

    @classmethod
    def empty(cls) -> WatchlistEntry:
        """Return empty watchlist entry (safe default)."""
        return cls()
