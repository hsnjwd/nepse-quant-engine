"""Provider system for the NEPSE Quant Engine data service.

Providers are pluggable data sources that implement ``BaseProvider``.
The ``HybridProvider`` chains multiple providers with automatic fallback:
if one provider fails, the next is tried transparently.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import requests

from src.config import DATA_DIRECTORY
from src.data.exceptions import DataUnavailable, ProviderError
from src.data.models import MarketSummary, StockQuote, StockHistory, TopMover, TopMovers
from src.loaders.csv_loader import load_csv as _load_csv_file

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Base provider
# ═══════════════════════════════════════════════════════════════════


class BaseProvider(ABC):
    """Abstract base for all data providers.

    Subclasses override the ``_do_*`` methods.  Callers use the public
    ``get_*`` methods which handle logging, error wrapping, and fallback.
    """

    name: str = "base"

    # ── Market summary ───────────────────────────────────────────

    @abstractmethod
    def _do_market_summary(self) -> MarketSummary:
        ...

    def get_market_summary(self) -> MarketSummary:
        try:
            logger.info("[%s] Fetching market summary...", self.name)
            return self._do_market_summary()
        except Exception as exc:
            logger.warning("[%s] market summary failed: %s", self.name, exc)
            raise ProviderError(str(exc)) from exc

    # ── Market status (convenience) ──────────────────────────────

    def get_market_status(self) -> str:
        """Return the market status string, e.g. 'Open' or 'Closed'."""
        try:
            return self.get_market_summary().status
        except Exception:
            return "Unknown"

    # ── Live quotes ──────────────────────────────────────────────

    @abstractmethod
    def _do_live_quotes(self) -> list[StockQuote]:
        ...

    def get_live_quotes(self) -> list[StockQuote]:
        try:
            logger.info("[%s] Fetching live quotes...", self.name)
            return self._do_live_quotes()
        except Exception as exc:
            logger.warning("[%s] live quotes failed: %s", self.name, exc)
            raise ProviderError(str(exc)) from exc

    def get_live_market(self) -> list[StockQuote]:
        """Alias for ``get_live_quotes()``."""
        return self.get_live_quotes()

    def get_quote(self, symbol: str) -> StockQuote | None:
        try:
            quotes = self.get_live_quotes()
        except Exception as exc:
            logger.debug("[%s] get_quote(%s) unavailable: %s", self.name, symbol, exc)
            return None
        for q in quotes:
            if q.symbol.upper() == symbol.upper().strip():
                return q
        return None

    # ── Price history ────────────────────────────────────────────

    @abstractmethod
    def _do_history(self, symbol: str, days: int) -> pd.DataFrame:
        ...

    def get_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        try:
            logger.info("[%s] Fetching history for %s (%d days)...", self.name, symbol, days)
            return self._do_history(symbol, days)
        except Exception as exc:
            logger.warning("[%s] history for %s failed: %s", self.name, symbol, exc)
            raise ProviderError(str(exc)) from exc

    # ── NEPSE index history ──────────────────────────────────────

    @abstractmethod
    def _do_nepse_index_history(self, days: int) -> pd.DataFrame:
        ...

    def get_nepse_index_history(self, days: int = 500) -> pd.DataFrame:
        """Fetch historical OHLCV data for the NEPSE index itself."""
        try:
            logger.info("[%s] Fetching NEPSE index history (%d days)...", self.name, days)
            return self._do_nepse_index_history(days)
        except NotImplementedError:
            logger.warning("[%s] NEPSE index history not supported", self.name)
            raise ProviderError(f"[{self.name}] NEPSE index history not supported")
        except Exception as exc:
            logger.warning("[%s] NEPSE index history failed: %s", self.name, exc)
            raise ProviderError(str(exc)) from exc

    # ── Top movers ───────────────────────────────────────────────

    def _do_top_gainers(self, limit: int) -> list[TopMover]:
        raise NotImplementedError

    def _do_top_losers(self, limit: int) -> list[TopMover]:
        raise NotImplementedError

    def _do_top_turnover(self, limit: int) -> list[TopMover]:
        raise NotImplementedError

    def get_top_gainers(self, limit: int = 10) -> list[TopMover]:
        try:
            return self._do_top_gainers(limit)
        except NotImplementedError as exc:
            raise ProviderError(f"[{self.name}] top gainers not supported") from exc
        except Exception as exc:
            logger.warning("[%s] top gainers failed: %s", self.name, exc)
            raise ProviderError(str(exc)) from exc

    def get_top_losers(self, limit: int = 10) -> list[TopMover]:
        try:
            return self._do_top_losers(limit)
        except NotImplementedError as exc:
            raise ProviderError(f"[{self.name}] top losers not supported") from exc
        except Exception as exc:
            logger.warning("[%s] top losers failed: %s", self.name, exc)
            raise ProviderError(str(exc)) from exc

    def get_top_turnover(self, limit: int = 10) -> list[TopMover]:
        try:
            return self._do_top_turnover(limit)
        except NotImplementedError as exc:
            raise ProviderError(f"[{self.name}] top turnover not supported") from exc
        except Exception as exc:
            logger.warning("[%s] top turnover failed: %s", self.name, exc)
            raise ProviderError(str(exc)) from exc


# ═══════════════════════════════════════════════════════════════════
# CSV provider — reads from local CSV files
# ═══════════════════════════════════════════════════════════════════


class CSVProvider(BaseProvider):
    """Reads data from local CSV files in DATA_DIRECTORY.

    Supports multiple directory layouts:
    - ``data/*.csv``
    - ``data/history/*.csv``
    - ``data/stocks/*.csv``
    """

    name: str = "csv"

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else Path(DATA_DIRECTORY)
        logger.info("[CSVProvider] Data directory: %s", self._data_dir)

    def _discover_csv_files(self) -> list[Path]:
        """Return all CSV files from known locations."""
        files: list[Path] = []
        for pattern in ("*.csv", "history/*.csv", "stocks/*.csv"):
            files.extend(sorted(self._data_dir.glob(pattern)))
        # Deduplicate
        seen: set[Path] = set()
        unique: list[Path] = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        return unique

    def _load_csv(self, file_path: Path) -> pd.DataFrame:
        """Load and clean a CSV file into an OHLCV DataFrame.

        Delegates to the single canonical CSV parser
        (``src.loaders.csv_loader.load_csv``).
        """
        return _load_csv_file(file_path)

    def _do_market_summary(self) -> MarketSummary:
        return MarketSummary(status="Unknown")

    def _do_live_quotes(self) -> list[StockQuote]:
        """Build quotes from the most recent row of each CSV file."""
        quotes: list[StockQuote] = []
        for f in self._discover_csv_files():
            if f.stem.lower() == "sample":
                continue
            try:
                df = self._load_csv(f)
                if df.empty:
                    continue
                if "Close" not in df.columns:
                    logger.debug("[CSVProvider] Skipping %s: no Close column", f.name)
                    continue
                latest = df.iloc[-1]
                quotes.append(StockQuote(
                    symbol=f.stem.upper(),
                    ltp=float(latest.get("Close", 0)),
                    open_price=float(latest.get("Open", 0)),
                    high=float(latest.get("High", 0)),
                    low=float(latest.get("Low", 0)),
                    close=float(latest.get("Close", 0)),
                    volume=int(latest.get("Volume", 0)),
                ))
            except Exception as exc:
                logger.debug("[CSVProvider] Skipping %s: %s", f.name, exc)
        return quotes

    def _do_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Load history for *symbol* from a CSV file."""
        symbol_lower = symbol.lower().strip()
        for f in self._discover_csv_files():
            if f.stem.lower() == symbol_lower:
                df = self._load_csv(f)
                if not df.empty and days > 0 and len(df) > days:
                    df = df.iloc[-days:]
                return df
        raise ProviderError(f"CSV file not found for {symbol}")

    def _do_nepse_index_history(self, days: int = 500) -> pd.DataFrame:
        """NEPSE index history is not available from local CSV files."""
        raise NotImplementedError("NEPSE index history not available from CSV provider")


# ═══════════════════════════════════════════════════════════════════
# API provider — fetches from REST APIs
# ═══════════════════════════════════════════════════════════════════


class APIProvider(BaseProvider):
    """Fetches data from NEPSE REST APIs.

    Supports multiple API sources configured via ``api_urls``:
    - ``nepse_scraper`` — community NEPSE scraper API
    - ``nepse_data_api`` — structured NEPSE data API
    - ``nepse_client`` — the NEPSE client API
    - ``nepalstock_official`` — official NEPSE exchange API
    - ``github_datasets`` — GitHub-hosted NEPSE datasets
    """

    name: str = "api"

    def __init__(self, api_urls: dict[str, str] | None = None) -> None:
        self._api_urls = api_urls or {}
        self._timeout = 15
        # Consecutive connection-level failures (host unreachable /
        # timeout).  Used to fail fast instead of hammering a dead host
        # thousands of times inside the history loops.
        self._consecutive_connection_failures = 0

    def _safe_float(self, v: Any, default: float = 0.0) -> float:
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def _safe_int(self, v: Any, default: int = 0) -> int:
        if v is None:
            return default
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    def _fetch_json(self, url: str) -> Any:
        resp = requests.get(url, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _try_urls(self, url_keys: list[str], path: str) -> Any:
        """Try multiple URL keys in order for *path*.

        Returns the first non-None payload, or ``None`` if all keys
        failed.  Connection-level failures (host unreachable / timeout)
        are counted so callers can abort early on a dead host; HTTP
        errors such as 404 (e.g. weekend shards) do not count.
        """
        for key in url_keys:
            base = self._api_urls.get(key)
            if not base:
                continue
            url = f"{base.rstrip('/')}/{path.lstrip('/')}"
            try:
                data = self._fetch_json(url)
                if data is not None:
                    self._consecutive_connection_failures = 0
                    return data
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                self._consecutive_connection_failures += 1
                logger.warning("[%s] connection error for %s", self.name, url)
                continue
            except Exception as exc:
                logger.debug("[%s] request failed for %s: %s", self.name, url, exc)
                continue
        return None

    def _do_market_summary(self) -> MarketSummary:
        # Try Suraj Rimal API first
        data = self._try_urls(["nepse_scraper", "nepse_data_api"], "market/status")
        if not data:
            data = self._try_urls(["github_datasets", "nepse_client"], "market/summary.json")

        if data and isinstance(data, dict):
            return MarketSummary(
                index=self._safe_float(data.get("index", data.get("nepseIndex", 0))),
                change=self._safe_float(data.get("change", data.get("pointChange", 0))),
                change_pct=self._safe_float(data.get("changePct", data.get("percentChange", 0))),
                volume=self._safe_int(data.get("volume", data.get("totalTradedShares", 0))),
                turnover=self._safe_float(data.get("turnover", data.get("totalTurnover", 0))),
                advances=self._safe_int(data.get("advances", 0)),
                declines=self._safe_int(data.get("declines", data.get("decreases", 0))),
                unchanged=self._safe_int(data.get("unchanged", 0)),
                status=str(data.get("status", data.get("marketStatus", "Unknown"))).capitalize(),
            )
        raise ProviderError("No API source returned market summary")

    def _do_live_quotes(self) -> list[StockQuote]:
        data = self._try_urls(["nepse_scraper", "nepse_data_api"], "market/live")
        if not data:
            data = self._try_urls(["github_datasets", "nepse_client"], "nepse_data.json")
            if data and isinstance(data, dict):
                data = data.get("data", data.get("stocks", []))

        if isinstance(data, list):
            return [
                StockQuote(
                    symbol=str(stk.get("symbol", "?")),
                    company_name=str(stk.get("companyName", stk.get("securityName", ""))),
                    ltp=self._safe_float(stk.get("ltp", stk.get("lastTradedPrice", 0))),
                    change=self._safe_float(stk.get("change", stk.get("pointChange", 0))),
                    change_pct=self._safe_float(stk.get("changePct", stk.get("percentChange", 0))),
                    open_price=self._safe_float(stk.get("open", stk.get("openPrice", 0))),
                    high=self._safe_float(stk.get("high", stk.get("highPrice", 0))),
                    low=self._safe_float(stk.get("low", stk.get("lowPrice", 0))),
                    close=self._safe_float(stk.get("close", stk.get("closePrice", 0))),
                    volume=self._safe_int(stk.get("volume", stk.get("totalTradedShares", 0))),
                    turnover=self._safe_float(stk.get("turnover", stk.get("totalTurnover", 0))),
                    previous_close=self._safe_float(stk.get("previousClose", stk.get("prevClose", 0))),
                )
                for stk in data if isinstance(stk, dict)
            ]
        raise ProviderError("No API source returned live quotes")

    def _do_top_gainers(self, limit: int = 10) -> list[TopMover]:
        return self._parse_top_movers(
            self._try_urls(["nepse_scraper"], "top/gainers"),
            limit, "gainers"
        )

    def _do_top_losers(self, limit: int = 10) -> list[TopMover]:
        return self._parse_top_movers(
            self._try_urls(["nepse_scraper"], "top/losers"),
            limit, "losers"
        )

    def _do_top_turnover(self, limit: int = 10) -> list[TopMover]:
        return self._parse_top_movers(
            self._try_urls(["nepse_scraper"], "top/turnover"),
            limit, "turnover"
        )

    def _parse_top_movers(self, data: Any, limit: int, label: str) -> list[TopMover]:
        if not data:
            return []
        if isinstance(data, list):
            results = []
            for item in data[:limit]:
                if not isinstance(item, dict):
                    continue
                results.append(TopMover(
                    symbol=str(item.get("symbol", "?")),
                    ltp=self._safe_float(item.get("ltp", item.get("closePrice", 0))),
                    change_pct=self._safe_float(item.get("changePct", item.get("percentChange", 0))),
                    turnover=self._safe_float(item.get("turnover", item.get("totalTurnover", 0))),
                    volume=self._safe_int(item.get("volume", item.get("totalTradedShares", 0))),
                ))
            return results
        return []

    def _do_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Build OHLCV history from daily shards."""
        today = date.today()
        records: list[dict[str, Any]] = []

        for offset in range(days):
            # Fail fast once the host looks unreachable: every later
            # day would fail the same way.
            if self._consecutive_connection_failures >= 3:
                logger.warning("[%s] API host unreachable; aborting history fetch", self.name)
                break
            d = today - timedelta(days=offset)
            data = self._try_urls(
                ["github_datasets", "nepse_client"],
                f"ltp/daily/{d.isoformat()}.json",
            )
            if not data:
                continue
            stocks = data if isinstance(data, list) else data.get("stocks", [])
            for stk in stocks:
                if not isinstance(stk, dict):
                    continue
                if str(stk.get("symbol", "")).upper() == symbol.upper().strip():
                    records.append({
                        "Date": d.isoformat(),
                        "Open": self._safe_float(stk.get("open", stk.get("openPrice", 0))),
                        "High": self._safe_float(stk.get("high", stk.get("highPrice", 0))),
                        "Low": self._safe_float(stk.get("low", stk.get("lowPrice", 0))),
                        "Close": self._safe_float(stk.get("close", stk.get("closePrice", stk.get("ltp", 0)))),
                        "Volume": self._safe_int(stk.get("volume", stk.get("totalTradedShares", 0))),
                    })
                    break

        if not records:
            raise ProviderError(f"No price history for {symbol}")

        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df

    def _do_nepse_index_history(self, days: int = 500) -> pd.DataFrame:
        """Fetch NEPSE index history from daily shards."""
        today = date.today()
        records: list[dict[str, Any]] = []

        for offset in range(days):
            # Fail fast once the host looks unreachable.
            if self._consecutive_connection_failures >= 3:
                logger.warning("[%s] API host unreachable; aborting index history fetch", self.name)
                break
            d = today - timedelta(days=offset)
            data = self._try_urls(
                ["github_datasets", "nepse_client"],
                f"ltp/daily/{d.isoformat()}.json",
            )
            if not data:
                continue
            if isinstance(data, dict):
                index_val = self._safe_float(data.get("nepseIndex", data.get("index", 0)))
                if index_val > 0:
                    records.append({
                        "Date": d.isoformat(),
                        "Open": index_val,
                        "High": index_val * 1.005,
                        "Low": index_val * 0.995,
                        "Close": index_val,
                        "Volume": self._safe_int(data.get("totalTradedShares", 0)),
                    })

        if not records:
            raise ProviderError("No NEPSE index history available")

        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df


# ═══════════════════════════════════════════════════════════════════
# Hybrid provider — chains providers with automatic fallback
# ═══════════════════════════════════════════════════════════════════


class HybridProvider(BaseProvider):
    """Chains multiple providers with automatic fallback on failure.

    Providers are tried in order.  If the first provider raises any
    exception, the next provider is tried transparently.  If all
    providers fail, the last exception is re-raised as ``DataUnavailable``.

    Supports optional health monitoring: when a ``ProviderHealthMonitor``
    is attached, unhealthy (disabled) providers are skipped automatically,
    and the *healthiest* provider (highest success rate) is tried first.

    Usage::

        provider = HybridProvider([
            APIProvider(api_urls={...}),
            CSVProvider(data_dir="data/raw"),
        ])
        summary = provider.get_market_summary()  # tries API → CSV
    """

    name: str = "hybrid"

    def __init__(
        self,
        providers: list[BaseProvider] | None = None,
        health_monitor: Any = None,
    ) -> None:
        self._providers = providers or []
        self._last_provider: str | None = None
        self._health_monitor = health_monitor

    @property
    def last_provider(self) -> str | None:
        """Name of the provider that served the last successful request."""
        return self._last_provider

    def _get_healthy_providers(self) -> list[BaseProvider]:
        """Return providers sorted by health (healthiest first).

        Skips disabled providers.  Falls back to original order if no
        health monitor is attached.
        """
        if self._health_monitor is None:
            return list(self._providers)

        healthy = []
        for p in self._providers:
            if self._health_monitor.is_healthy(p.name):
                healthy.append(p)

        # Sort by success rate descending (healthiest first)
        def _success_rate(p: BaseProvider) -> float:
            h = self._health_monitor.get_health(p.name)
            return h.success_rate if h else 100.0

        healthy.sort(key=_success_rate, reverse=True)
        return healthy

    def _try_all(self, method: str, *args: Any, **kwargs: Any) -> Any:
        errors: list[str] = []
        providers_to_try = self._get_healthy_providers()

        if not providers_to_try:
            # All providers disabled — try original list as fallback
            providers_to_try = list(self._providers)
            logger.warning("[Hybrid] All providers disabled; trying originals")

        for provider in providers_to_try:
            try:
                func = getattr(provider, method)
                result = func(*args, **kwargs)
                self._last_provider = provider.name
                logger.info("[Hybrid] %s succeeded via %s", method, provider.name)
                return result
            except Exception as exc:
                errors.append(f"[{provider.name}] {exc}")
                logger.warning("[Hybrid] %s failed via %s: %s", method, provider.name, exc)
                continue
        raise DataUnavailable(
            f"All providers failed for {method}: {'; '.join(errors)}"
        )

    def get_market_summary(self) -> MarketSummary:
        return self._try_all("get_market_summary")

    def get_live_quotes(self) -> list[StockQuote]:
        return self._try_all("get_live_quotes")

    def get_quote(self, symbol: str) -> StockQuote | None:
        return self._try_all("get_quote", symbol)

    def get_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        return self._try_all("get_history", symbol, days)

    def get_nepse_index_history(self, days: int = 500) -> pd.DataFrame:
        return self._try_all("get_nepse_index_history", days)

    def get_top_gainers(self, limit: int = 10) -> list[TopMover]:
        return self._try_all("get_top_gainers", limit)

    def get_top_losers(self, limit: int = 10) -> list[TopMover]:
        return self._try_all("get_top_losers", limit)

    def get_top_turnover(self, limit: int = 10) -> list[TopMover]:
        return self._try_all("get_top_turnover", limit)

    # HybridProvider delegates all _do_* calls via _try_all.
    # These stubs satisfy the abstract interface but are never called
    # because HybridProvider overrides get_market_summary, get_live_quotes,
    # etc. to route through _try_all instead.
    def _do_market_summary(self) -> MarketSummary:
        raise NotImplementedError("HybridProvider uses _try_all, not _do_*")

    def _do_live_quotes(self) -> list[StockQuote]:
        raise NotImplementedError("HybridProvider uses _try_all, not _do_*")

    def _do_history(self, symbol: str, days: int) -> pd.DataFrame:
        raise NotImplementedError("HybridProvider uses _try_all, not _do_*")

    def _do_nepse_index_history(self, days: int) -> pd.DataFrame:
        raise NotImplementedError("HybridProvider uses _try_all, not _do_*")
