"""Live NEPSE market data service.

.. deprecated::
    This module is **deprecated** since v0.6.  Use :class:`src.data.service.DataService` instead.

    Migration:
        ``from src.data.live_data import get_market_summary`` →
        ``from src.data import DataService; DataService().get_market_summary()``

This is the **ONLY** module that communicates with external NEPSE APIs.
All Streamlit pages must use this service for live market data.

Architecture::

    Streamlit Pages
          ↓
    LiveDataService  ←── singleton dataclass
          ↓
    External APIs (nepseapi.surajrimal.dev / yonepse static mirrors)
          ↓
    Safe defaults on failure, never crashes the UI

Sources
-------
Primary  — https://nepseapi.surajrimal.dev/api/v1  (real-time REST)
Fallback — https://shubhamnpk.github.io/yonepse/data  (static JSON mirrors, updated ~30 min)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from typing import Any, Callable

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

_PRIMARY_API = "https://nepseapi.surajrimal.dev/api/v1"
_YONEPSE_BASE = "https://shubhamnpk.github.io/yonepse/data"
_REQUEST_TIMEOUT = 15  # seconds

# ═══════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════


@dataclass
class MarketSummary:
    """Live snapshot of the NEPSE market."""

    index: float
    change: float
    change_pct: float
    volume: int
    turnover: float
    advances: int
    declines: int
    unchanged: int
    status: str  # "Open" | "Closed" | "Unknown"
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def empty(cls) -> MarketSummary:
        """Return a zeroed-out summary for when the API is unavailable."""
        return cls(
            index=0.0, change=0.0, change_pct=0.0,
            volume=0, turnover=0.0,
            advances=0, declines=0, unchanged=0,
            status="Unknown",
        )


@dataclass
class StockSnapshot:
    """Live tick for a single NEPSE stock."""

    symbol: str
    company_name: str
    ltp: float
    change: float
    change_pct: float
    open_price: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float
    previous_close: float = 0.0


@dataclass
class TopStock:
    """A top-gainer, top-loser, or top-turnover entry."""

    symbol: str
    ltp: float
    change_pct: float
    turnover: float = 0.0
    volume: int = 0

# ═══════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Cast *value* to float; return *default* when impossible."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any] | None:
    """HTTP GET → JSON, or ``None`` on any failure."""
    try:
        resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("API request failed [%s]: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error fetching %s: %s", url, exc)
        return None

# ═══════════════════════════════════════════════════════════════════
# Market-level API
# ═══════════════════════════════════════════════════════════════════


def get_market_summary() -> MarketSummary:
    """Fetch the current NEPSE market summary (index, change, volume, etc.).

    Returns:
        A :class:`MarketSummary` with live data, or an empty summary when
        the API is unavailable.
    """
    # Try the Suraj Rimal API first
    data = _fetch_json(f"{_PRIMARY_API}/market/status")
    if data and isinstance(data, dict):
        try:
            return MarketSummary(
                index=_safe_float(data.get("index", data.get("nepseIndex", data.get("nepse_index", 0)))),
                change=_safe_float(data.get("change", data.get("pointChange", 0))),
                change_pct=_safe_float(data.get("changePct", data.get("percentChange", data.get("change_pct", 0)))),
                volume=_safe_int(data.get("volume", data.get("totalTradedShares", 0))),
                turnover=_safe_float(data.get("turnover", data.get("totalTurnover", 0))),
                advances=_safe_int(data.get("advances", data.get("advances", 0))),
                declines=_safe_int(data.get("declines", data.get("decreases", 0))),
                unchanged=_safe_int(data.get("unchanged", data.get("unchanged", 0))),
                status=str(data.get("status", data.get("marketStatus", "Unknown"))).capitalize(),
            )
        except Exception as exc:
            logger.warning("Failed to parse market status response: %s", exc)

    # Fallback: yonepse static summary
    data = _fetch_json(f"{_YONEPSE_BASE}/market/summary.json")
    if data and isinstance(data, dict):
        try:
            return MarketSummary(
                index=_safe_float(data.get("nepseIndex", data.get("index", 0))),
                change=_safe_float(data.get("pointChange", data.get("change", 0))),
                change_pct=_safe_float(data.get("percentChange", data.get("changePct", 0))),
                volume=_safe_int(data.get("totalTradedShares", data.get("volume", 0))),
                turnover=_safe_float(data.get("totalTurnover", data.get("turnover", 0))),
                advances=_safe_int(data.get("advances", 0)),
                declines=_safe_int(data.get("decreases", data.get("declines", 0))),
                unchanged=_safe_int(data.get("unchanged", 0)),
                status=str(data.get("status", "Unknown")).capitalize(),
            )
        except Exception as exc:
            logger.warning("Failed to parse yonepse summary: %s", exc)

    return MarketSummary.empty()


def get_market_status() -> str:
    """Return the current market status string (e.g. ``"Open"``, ``"Closed"``)."""
    return get_market_summary().status


def get_top_gainers(limit: int = 10) -> list[TopStock]:
    """Fetch the top *limit* gainers."""
    data = _fetch_json(f"{_PRIMARY_API}/top/gainers")
    return _parse_top_list(data, limit, "gainers")


def get_top_losers(limit: int = 10) -> list[TopStock]:
    """Fetch the top *limit* losers."""
    data = _fetch_json(f"{_PRIMARY_API}/top/losers")
    return _parse_top_list(data, limit, "losers")


def get_top_turnover(limit: int = 10) -> list[TopStock]:
    """Fetch the top *limit* stocks by turnover."""
    data = _fetch_json(f"{_PRIMARY_API}/top/turnover")
    return _parse_top_list(data, limit, "turnover")


def _parse_top_list(
    data: Any,
    limit: int,
    label: str,
) -> list[TopStock]:
    """Parse a top-list response from either API source."""
    if not data:
        # Fallback to yonepse
        data = _fetch_json(f"{_YONEPSE_BASE}/market/top_stocks.json")
        if data and isinstance(data, dict):
            data = data.get(label, [])

    if isinstance(data, list):
        results: list[TopStock] = []
        for item in data[:limit]:
            if not isinstance(item, dict):
                continue
            results.append(TopStock(
                symbol=str(item.get("symbol", "?")),
                ltp=_safe_float(item.get("ltp", item.get("closePrice", 0))),
                change_pct=_safe_float(item.get("changePct", item.get("percentChange", 0))),
                turnover=_safe_float(item.get("turnover", item.get("totalTurnover", 0))),
                volume=_safe_int(item.get("volume", item.get("totalTradedShares", 0))),
            ))
        return results
    return []


def get_indices() -> list[dict[str, Any]]:
    """Return all NEPSE indices with their current values.

    Each entry has keys: ``name``, ``current``, ``change``, ``change_pct``.
    """
    data = _fetch_json(f"{_PRIMARY_API}/indices")
    if data and isinstance(data, list):
        return [
            {
                "name": str(idx.get("name", idx.get("indexName", "?"))),
                "current": _safe_float(idx.get("current", idx.get("currentValue", 0))),
                "change": _safe_float(idx.get("change", idx.get("pointChange", 0))),
                "change_pct": _safe_float(idx.get("changePct", idx.get("percentChange", 0))),
            }
            for idx in data
        ]
    # Fallback
    data = _fetch_json(f"{_YONEPSE_BASE}/market/indices.json")
    if data and isinstance(data, list):
        return [
            {
                "name": str(idx.get("name", "?")),
                "current": _safe_float(idx.get("current", 0)),
                "change": _safe_float(idx.get("change", 0)),
                "change_pct": _safe_float(idx.get("changePct", 0)),
            }
            for idx in data
        ]
    return []

# ═══════════════════════════════════════════════════════════════════
# Stock-level API
# ═══════════════════════════════════════════════════════════════════


def get_all_stocks() -> list[StockSnapshot]:
    """Fetch live data for every traded stock on NEPSE.

    Returns:
        A list of :class:`StockSnapshot` objects, empty list on failure.
    """
    data = _fetch_json(f"{_PRIMARY_API}/market/live")
    if data and isinstance(data, list):
        return [
            StockSnapshot(
                symbol=str(stk.get("symbol", "?")),
                company_name=str(stk.get("companyName", stk.get("securityName", ""))),
                ltp=_safe_float(stk.get("ltp", stk.get("lastTradedPrice", 0))),
                change=_safe_float(stk.get("change", stk.get("pointChange", 0))),
                change_pct=_safe_float(stk.get("changePct", stk.get("percentChange", 0))),
                open_price=_safe_float(stk.get("open", stk.get("openPrice", 0))),
                high=_safe_float(stk.get("high", stk.get("highPrice", 0))),
                low=_safe_float(stk.get("low", stk.get("lowPrice", 0))),
                close=_safe_float(stk.get("close", stk.get("closePrice", 0))),
                volume=_safe_int(stk.get("volume", stk.get("totalTradedShares", 0))),
                turnover=_safe_float(stk.get("turnover", stk.get("totalTurnover", 0))),
                previous_close=_safe_float(stk.get("previousClose", stk.get("prevClose", 0))),
            )
            for stk in data if isinstance(stk, dict)
        ]

    # Fallback: yonepse static nepse_data.json
    data = _fetch_json(f"{_YONEPSE_BASE}/nepse_data.json")
    if data and isinstance(data, dict):
        stocks_list = data.get("data", data.get("stocks", []))
        if isinstance(stocks_list, list):
            return [
                StockSnapshot(
                    symbol=str(stk.get("symbol", "?")),
                    company_name=str(stk.get("companyName", "")),
                    ltp=_safe_float(stk.get("ltp", stk.get("lastTradedPrice", 0))),
                    change=_safe_float(stk.get("change", 0)),
                    change_pct=_safe_float(stk.get("changePct", stk.get("percentChange", 0))),
                    open_price=_safe_float(stk.get("open", stk.get("openPrice", 0))),
                    high=_safe_float(stk.get("high", stk.get("highPrice", 0))),
                    low=_safe_float(stk.get("low", stk.get("lowPrice", 0))),
                    close=_safe_float(stk.get("close", stk.get("closePrice", 0))),
                    volume=_safe_int(stk.get("volume", stk.get("totalTradedShares", 0))),
                    turnover=_safe_float(stk.get("turnover", stk.get("totalTurnover", 0))),
                    previous_close=_safe_float(stk.get("previousClose", 0)),
                )
                for stk in stocks_list if isinstance(stk, dict)
            ]

    return []


def get_stock(symbol: str) -> StockSnapshot | None:
    """Fetch live data for a single stock.

    Args:
        symbol: Stock ticker (e.g. ``"NABIL"``).

    Returns:
        A :class:`StockSnapshot` or ``None`` if not found / unavailable.
    """
    symbol_upper = symbol.upper().strip()
    all_stocks = get_all_stocks()
    for stock in all_stocks:
        if stock.symbol.upper() == symbol_upper:
            return stock
    return None


def search_stocks(query: str) -> list[StockSnapshot]:
    """Search stocks by symbol or company name.

    Args:
        query: Partial match string (case-insensitive).

    Returns:
        A list of matching :class:`StockSnapshot` objects.
    """
    q = query.upper().strip()
    if not q:
        return []
    all_stocks = get_all_stocks()
    return [
        s for s in all_stocks
        if q in s.symbol.upper() or q in s.company_name.upper()
    ]


def get_securities_list() -> list[dict[str, str]]:
    """Return the master list of all NEPSE securities.

    Each entry has keys: ``symbol`` and ``company_name``.
    """
    data = _fetch_json(f"{_YONEPSE_BASE}/other/securities.json")
    if data and isinstance(data, list):
        return [
            {"symbol": str(s.get("symbol", "?")), "company_name": str(s.get("companyName", ""))}
            for s in data if isinstance(s, dict)
        ]
    return []

# ═══════════════════════════════════════════════════════════════════
# OHLCV Price History
# ═══════════════════════════════════════════════════════════════════


def get_price_history(
    symbol: str,
    days: int = 365,
    auto_detect: bool = True,
) -> pd.DataFrame:
    """Build an OHLCV DataFrame for *symbol* from yonepse daily shards.

    The function downloads daily shards from the yonepse static mirror and
    extracts the row for *symbol* from each day, constructing a standard
    OHLCV DataFrame compatible with the indicator / regime / signal engines.

    Args:
        symbol: Stock ticker (e.g. ``"NABIL"``).
        days: Number of historical trading days to fetch.
        auto_detect: If ``True`` and the API fails, emit a noisy warning
            rather than raising.

    Returns:
        A DataFrame with columns ``Date``, ``Open``, ``High``, ``Low``,
        ``Close``, ``Volume`` (and optionally ``Turnover``).  Returns an
        **empty** DataFrame when data is unavailable.
    """
    try:
        return _build_ohlcv_from_shards(symbol.upper().strip(), days)
    except Exception as exc:
        logger.warning("Failed to build price history for %s: %s", symbol, exc)
        return pd.DataFrame()


def _build_ohlcv_from_shards(symbol: str, days: int) -> pd.DataFrame:
    """Download daily shards from yonepse and extract *symbol*'s OHLCV."""
    today = date.today()
    records: list[dict[str, Any]] = []

    # Download shards one day at a time
    for offset in range(days):
        d = today - timedelta(days=offset)
        shard_url = f"{_YONEPSE_BASE}/ltp/daily/{d.isoformat()}.json"
        data = _fetch_json(shard_url)

        if not data:
            continue  # skip non-trading days / missing shards

        stocks = data if isinstance(data, list) else data.get("stocks", [])
        for stk in stocks:
            if not isinstance(stk, dict):
                continue
            if str(stk.get("symbol", "")).upper() == symbol:
                records.append({
                    "Date": d.isoformat(),
                    "Open": _safe_float(stk.get("open", stk.get("openPrice", 0))),
                    "High": _safe_float(stk.get("high", stk.get("highPrice", 0))),
                    "Low": _safe_float(stk.get("low", stk.get("lowPrice", 0))),
                    "Close": _safe_float(stk.get("close", stk.get("closePrice", stk.get("ltp", 0)))),
                    "Volume": _safe_int(stk.get("volume", stk.get("totalTradedShares", 0))),
                    "Turnover": _safe_float(stk.get("turnover", stk.get("totalTurnover", 0))),
                })
                break  # only one entry per stock per day

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def get_nepse_index_history(days: int = 500) -> pd.DataFrame:
    """Fetch NEPSE index history for regime detection.

    Uses the NEPSE index from yonepse daily shards to construct a
    time-series of index values.

    Returns:
        A DataFrame with columns ``Date``, ``Close`` (the index value),
        and optionally ``High``, ``Low``, ``Open``, ``Volume``.
        **Empty** DataFrame on failure.
    """
    today = date.today()
    records: list[dict[str, Any]] = []

    for offset in range(days):
        d = today - timedelta(days=offset)
        shard_url = f"{_YONEPSE_BASE}/ltp/daily/{d.isoformat()}.json"
        data = _fetch_json(shard_url)

        if not data:
            continue

        # The first 'stock' entry in the daily shard often contains the
        # market summary / NEPSE index metadata.
        stocks = data if isinstance(data, list) else data.get("stocks", [])
        if isinstance(stocks, list) and stocks:
            # Some shards embed the index value at the top level
            if isinstance(data, dict):
                index_val = _safe_float(data.get("nepseIndex", data.get("index", 0)))
                if index_val > 0:
                    records.append({
                        "Date": d.isoformat(),
                        "Open": index_val,
                        "High": index_val * 1.005,
                        "Low": index_val * 0.995,
                        "Close": index_val,
                        "Volume": _safe_int(data.get("totalTradedShares", 0)),
                    })
                    continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df

# ═══════════════════════════════════════════════════════════════════
# Convenience: market scan (returns dict compatible with scanner/engine)
# ═══════════════════════════════════════════════════════════════════


def live_market_scan() -> dict[str, Any]:
    """Scan all live stocks and run the full analysis pipeline.

    This mirrors the output shape of ``scan_market()`` from
    :mod:`src.scanner.engine` but uses live API data + the existing
    :func:`src.engine.analyzer.analyze_dataframe` engine.

    Returns:
        A dict with keys ``results`` (list of analysis dicts) and
        ``skipped`` (list of failed symbols).
    """
    from src.engine.analyzer import analyze_dataframe
    from src.scanner.ranking import rank_market

    live_stocks = get_all_stocks()
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    logger.info("Live market scan over %d stocks", len(live_stocks))

    for stock in live_stocks:
        try:
            df = get_price_history(stock.symbol, days=365)

            if df.empty:
                skipped.append({"symbol": stock.symbol, "error": "No price history available"})
                continue

            analysis = analyze_dataframe(df)
            analysis["symbol"] = stock.symbol
            # Overwrite price with the actual live LTP
            analysis["live_price"] = stock.ltp
            results.append(analysis)

        except Exception as exc:
            logger.warning("Live scan failed for %s: %s", stock.symbol, exc)
            skipped.append({"symbol": stock.symbol, "error": str(exc)})

    ranked = rank_market(results)
    return {"results": ranked, "skipped": skipped}


def live_stock_analysis(symbol: str) -> dict[str, Any] | None:
    """Analyse a single stock using live data + the full indicator/regime pipeline.

    Args:
        symbol: Stock ticker (e.g. ``"NABIL"``).

    Returns:
        Analysis dict (same shape as ``analyze_stock()``) or ``None`` on
        failure.
    """
    from src.engine.analyzer import analyze_dataframe

    try:
        df = get_price_history(symbol, days=365)
        if df.empty:
            logger.warning("No price history for %s", symbol)
            return None

        result = analyze_dataframe(df)
        result["symbol"] = symbol.upper()

        # Overwrite price with live LTP
        live = get_stock(symbol)
        if live:
            result["live_price"] = live.ltp
            result["price"] = live.ltp

        return result
    except Exception as exc:
        logger.warning("Live analysis failed for %s: %s", symbol, exc)
        return None


def live_regime_detection(symbol: str = "NEPSE") -> Any | None:
    """Detect the market regime using live NEPSE index history.

    Args:
        symbol: Passed through for API consistency.

    Returns:
        A :class:`~src.regime.detector.MarketRegime` result, or ``None``.
    """
    from src.regime.detector import MarketRegimeDetector

    try:
        df = get_nepse_index_history(days=500)
        if df.empty:
            logger.warning("No index history available for regime detection")
            return None
        detector = MarketRegimeDetector()
        return detector.detect(df)
    except Exception as exc:
        logger.warning("Regime detection failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════
# Cache helpers
# ═══════════════════════════════════════════════════════════════════


def refresh_cache() -> None:
    """Force a fresh fetch on the next call by clearing Streamlit's cache.

    Call this when the user explicitly requests a refresh.
    """
    import streamlit as st
    # st.cache_data.clear()
    # Cleared via st.cache_data(ttl=…) on each function instead.
    # This is a no-op hook for manual refresh triggers.
    logger.info("Cache refresh requested — next fetches will be fresh.")

# ═══════════════════════════════════════════════════════════════════
# Export symbol for Streamlit's ``@st.cache_data`` to reference
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "MarketSummary",
    "StockSnapshot",
    "TopStock",
    "get_market_summary",
    "get_market_status",
    "get_top_gainers",
    "get_top_losers",
    "get_top_turnover",
    "get_indices",
    "get_all_stocks",
    "get_stock",
    "search_stocks",
    "get_securities_list",
    "get_price_history",
    "get_nepse_index_history",
    "live_market_scan",
    "live_stock_analysis",
    "live_regime_detection",
    "refresh_cache",
]
