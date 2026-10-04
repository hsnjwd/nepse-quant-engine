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

from src.config import API_TIMEOUT, DATA_DIRECTORY
from src.data.exceptions import (
    DataUnavailable,
    InvalidDataError,
    ProviderError,
    ProviderTimeout,
)
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
        except ProviderTimeout:
            # Sprint 13.2: preserve the distinguishable timeout category
            # through the wrapper (otherwise the generic wrap below would
            # convert it back into a plain ProviderError).
            raise
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
        except ProviderTimeout:
            raise
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
        except ProviderTimeout:
            raise
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
        except ProviderTimeout:
            raise
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
        # Deduplicate by symbol stem: the supported layouts are
        # alternative locations for the *same* symbol, so a file present
        # in both ``data/`` and ``data/history/`` must never surface
        # twice (Sprint 13.3 symbol-integrity: one symbol == one data
        # source; a duplicated quote/history would silently double-count
        # the company).
        seen: set[str] = set()
        unique: list[Path] = []
        for f in files:
            key = f.stem.lower()
            if key not in seen:
                seen.add(key)
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
        # Sprint 13.2: the per-request timeout now comes from the
        # central configuration (``API_TIMEOUT``, env-overridable, used
        # by docker-compose) instead of a hard-coded 15 s that ignored
        # the documented setting.  Default remains 15 s.
        self._timeout = float(API_TIMEOUT)
        # Consecutive connection-level failures (host unreachable /
        # timeout).  Used to fail fast instead of hammering a dead host
        # thousands of times inside the history loops.
        self._consecutive_connection_failures = 0
        # Category of the last failure observed by ``_try_urls``:
        # "timeout" | "connection" | "http" | None.  Lets the ``_do_*``
        # methods raise a distinguishable :class:`ProviderTimeout` when
        # an upstream timeout (not a refused connection / HTTP error /
        # malformed payload) was the reason every source failed
        # (Sprint 13.2).
        self._last_failure_kind: str | None = None

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

    def _raise_for_last_failure(self, message: str) -> None:
        """Raise the failure category observed by ``_try_urls``.

        A timeout is surfaced as :class:`ProviderTimeout` (distinct),
        every other failure mode as a generic :class:`ProviderError`
        (Sprint 13.2).  Both share the same message, so callers that
        catch ``ProviderError`` keep working unchanged.
        """
        if self._last_failure_kind == "timeout":
            raise ProviderTimeout(message) from None
        raise ProviderError(message)

    @staticmethod
    def _is_timeout_cause(exc: BaseException) -> bool:
        """Return True when *exc* or its cause-chain indicates a timeout.

        ``requests`` folds socket-level read/connect timeouts into
        ``requests.exceptions.ConnectionError`` whose cause is a
        ``urllib3.exceptions.ReadTimeoutError`` (or ``socket.timeout``,
        which IS the builtin ``TimeoutError``) — so distinguishing a
        genuine upstream stall from a refused connection requires
        walking the cause chain, not just the outer type (Sprint 13.2
        review follow-up: the failure-kind tracker must not lose the
        timeout distinction for these shapes).
        """
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, TimeoutError):
                return True
            # urllib3 defines its own ReadTimeoutError /
            # ConnectTimeoutError (not builtin TimeoutError subclasses).
            if type(current).__name__ in ("ReadTimeoutError", "ConnectTimeoutError"):
                return True
            current = current.__cause__ if current.__cause__ is not None else current.__context__
        return False

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
                    self._last_failure_kind = None
                    return data
            except requests.exceptions.Timeout as exc:
                # Sprint 13.2: record the failure *category* so the
                # ``_do_*`` methods can raise a distinguishable
                # ``ProviderTimeout`` (vs a generic ``ProviderError``
                # for refused connections / HTTP errors).  Logged at
                # debug to avoid noisy per-day timeout spam in history
                # loops; the final failure is reported once at the
                # ``_do_*`` boundary with the category attached.
                self._consecutive_connection_failures += 1
                self._last_failure_kind = "timeout"
                logger.debug("[%s] request timeout for %s", self.name, url)
                continue
            except requests.exceptions.ConnectionError as exc:
                self._consecutive_connection_failures += 1
                # ``requests`` raises socket-level read/connect timeouts
                # as ConnectionError with a timeout cause (urllib3
                # ReadTimeoutError / socket.timeout) — classify those as
                # "timeout" so ``ProviderTimeout`` stays distinguishable
                # for genuine upstream stalls (Sprint 13.2 review fix).
                self._last_failure_kind = (
                    "timeout" if self._is_timeout_cause(exc) else "connection"
                )
                logger.warning(
                    "[%s] %s for %s", self.name, self._last_failure_kind, url
                )
                continue
            except Exception as exc:
                self._last_failure_kind = "http"
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
        # If the dict path was skipped because *every* dict-format probe
        # timed out, surface that distinctly rather than falling into
        # the yonepse assembly (which would probe again and eventually
        # raise a generic ProviderError).  Sprint 13.2 distinguishable
        # timeout path.
        if not data and self._last_failure_kind == "timeout":
            self._raise_for_last_failure("No API source returned market summary (timeout)")

        # The dict-format APIs above are dead/unreachable.  Assemble the
        # summary from yonepse's static-data files, which split the same
        # snapshot across three endpoints (see _yonepse_market_summary).
        # When *data* is already the list-shaped ``market/summary.json``
        # payload, hand it to the helper so it is not fetched twice.
        summary = self._yonepse_market_summary(
            summary_payload=data if isinstance(data, list) else None
        )
        if summary is not None:
            return summary
        # Sprint 13.2: surface the failure category observed while
        # assembling the yonepse summary (a timeout stays a
        # ``ProviderTimeout`` instead of degrading to a generic error).
        self._raise_for_last_failure("No API source returned market summary")

    def _yonepse_market_summary(self, summary_payload: Any = None) -> MarketSummary | None:
        """Assemble a market summary from yonepse's static JSON files.

        yonepse (``https://shubhamnpk.github.io/yonepse/data``) exposes the
        market snapshot as three separate files rather than the single
        dict-shaped ``market/status`` payload of the (now-dead) scraper API:

        - ``market/status.json`` — ``{"is_open": bool, "last_checked": ...}``
        - ``market/summary.json`` — a *list* of ``{"detail", "value"}`` rows
          (Total Turnover, Total Traded Shares, Total Transactions, ...);
          the previous dict-only parser rejected this shape and the whole
          summary fell back to ``MarketSummary.empty()``.
        - ``indices.json`` — a list of index snapshots including the
          ``"NEPSE Index"`` entry (``close``/``currentValue``,
          ``change``, ``perChange``) — the only live source of the index.

        Returns a merged :class:`MarketSummary` or ``None`` when every
        probe failed (host unreachable), so the caller can raise.
        """
        summary = MarketSummary()
        got = False

        # Market open/closed status.
        status_data = self._try_urls(
            ["github_datasets", "nepse_client"], "market/status.json"
        )
        if isinstance(status_data, dict) and "is_open" in status_data:
            got = True
            summary.status = "Open" if status_data.get("is_open") else "Closed"

        # Turnover / traded shares (list of {detail, value} rows).
        # Reuse the payload already fetched by ``_do_market_summary``
        # when it is the list-shaped summary (avoids a duplicate GET).
        if isinstance(summary_payload, list):
            summary_data = summary_payload
        else:
            summary_data = self._try_urls(
                ["github_datasets", "nepse_client"], "market/summary.json"
            )
        if isinstance(summary_data, list):
            got = True
            for row in summary_data:
                if not isinstance(row, dict):
                    continue
                detail = str(row.get("detail", "")).lower()
                if "turnover" in detail:
                    summary.turnover = self._safe_float(row.get("value"))
                elif "shares" in detail:
                    summary.volume = self._safe_int(row.get("value"))

        # NEPSE index value / change (list of index objects).
        index_data = self._try_urls(["github_datasets", "nepse_client"], "indices.json")
        if not isinstance(index_data, list):
            index_data = self._try_urls(
                ["github_datasets", "nepse_client"], "market/indices.json"
            )
        if isinstance(index_data, list):
            for entry in index_data:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("index", "")).strip().lower() != "nepse index":
                    continue
                got = True
                summary.index = self._safe_float(
                    entry.get("currentValue", entry.get("close", 0))
                )
                summary.change = self._safe_float(entry.get("change", 0))
                summary.change_pct = self._safe_float(entry.get("perChange", 0))
                break

        return summary if got else None

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
        self._raise_for_last_failure("No API source returned live quotes")

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
            # Sprint 13.2: if the loop aborted because the host timed
            # out (fail-fast threshold), surface ``ProviderTimeout``
            # instead of a generic ``ProviderError`` so operators can
            # distinguish upstream timeouts from genuinely-absent data.
            if self._last_failure_kind == "timeout":
                self._raise_for_last_failure(f"No price history for {symbol} (timeout)")
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
            if self._last_failure_kind == "timeout":
                self._raise_for_last_failure("No NEPSE index history available (timeout)")
            raise ProviderError("No NEPSE index history available")

        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df


# ═══════════════════════════════════════════════════════════════════
# Second provider adapter (Sprint 13.6 §3)
# ═══════════════════════════════════════════════════════════════════


class GitHubCSVProvider(BaseProvider):
    """Independent second-provider adapter reading per-symbol OHLCV CSVs.

    Implements the *existing* ``BaseProvider`` interface so it plugs into
    the ``HybridProvider`` chain / reconciliation path with no
    architectural change and a preserved provider identity
    (``name = "github_csv"``).

    Target sources (independently sourced NEPSE archives — NOT a wrapper
    or cached copy of the primary yonepse/surajrimal pipeline):

    - ``Aabishkar2/nepse-data`` — company-wise historical CSVs maintained
      by an independent scraper pipeline (GitHub Actions cron).
    - ``omitnomis/ShareSansarScraper`` — ShareSansar-derived daily
      archive.

    The adapter is **disabled by default** (``SECOND_PROVIDER_URL`` is
    empty).  Live reliability of either archive is NOT verified in this
    environment; an operator who validates a base URL can engage it via
    ``SECOND_PROVIDER_URL``.  All tests exercise the adapter with a
    controlled file/HTTP double (``VERIFIED WITH CONTROLLED PROVIDER``),
    never fabricated live results.
    """

    name: str = "github_csv"

    def __init__(self, base_url: str | None = None) -> None:
        from src.config import SECOND_PROVIDER_URL  # noqa: PLC0415 - lazy

        self._base_url = (base_url or SECOND_PROVIDER_URL).rstrip("/")
        self._timeout = float(API_TIMEOUT)

    @property
    def base_url(self) -> str:
        return self._base_url

    def _history_url(self, symbol: str) -> str:
        """URL of the per-symbol CSV for *symbol*."""
        return f"{self._base_url}/{symbol.upper().strip()}.csv"

    def _parse_history_csv(self, text: str, symbol: str) -> pd.DataFrame:
        """Parse a company-wise CSV into the canonical OHLCV contract.

        Tolerates common column spellings (``date``/``Date``,
        ``open``/``Open``, ``high``/``High``, ``low``/``Low``,
        ``close``/``Close``/``ltp``, ``volume``/``traded_quantity`` /
        ``traded_quantity``).  Returns an empty frame when no usable
        OHLCV rows exist (the hybrid validator rejects empties).
        """
        import io  # noqa: PLC0415 - lazy

        df = pd.read_csv(io.StringIO(text))
        if df.empty:
            return df
        df.columns = [str(c).strip().lower() for c in df.columns]
        rename = {
            "date": "Date",
            "open": "Open",
            "open_price": "Open",
            "high": "High",
            "high_price": "High",
            "low": "Low",
            "low_price": "Low",
            "close": "Close",
            "close_price": "Close",
            "ltp": "Close",
            "volume": "Volume",
            "traded_quantity": "Volume",
            "traded_volume": "Volume",
            "traded_quantity": "Volume",
        }
        df = df.rename(columns=rename)
        needed = ("Date", "Open", "High", "Low", "Close", "Volume")
        missing = [c for c in needed if c not in df.columns]
        if missing:
            return pd.DataFrame()
        df = df[list(needed)]  # canonical column order (stable, not set-derived)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
        df = df.reset_index(drop=True)
        return df

    def _do_market_summary(self) -> MarketSummary:
        return MarketSummary(status="Unknown")

    def _do_live_quotes(self) -> list[StockQuote]:
        raise NotImplementedError("github_csv provides history only")

    def _do_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Fetch and parse the per-symbol OHLCV CSV."""
        import requests as _requests  # noqa: PLC0415 - lazy

        url = self._history_url(symbol)
        try:
            resp = _requests.get(url, timeout=self._timeout)
            resp.raise_for_status()
        except _requests.exceptions.Timeout as exc:
            raise ProviderTimeout(f"github_csv timeout for {symbol}") from exc
        except Exception as exc:
            raise ProviderError(f"github_csv fetch failed for {symbol}: {exc}") from exc
        df = self._parse_history_csv(resp.text, symbol)
        if df.empty:
            raise ProviderError(f"No price history for {symbol} (github_csv)")
        if len(df) > days > 0:
            df = df.iloc[-days:]
        return df

    def _do_nepse_index_history(self, days: int = 500) -> pd.DataFrame:
        raise NotImplementedError("NEPSE index history not available from github_csv")


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
        data_validator: Any = None,
    ) -> None:
        self._providers = providers or []
        self._last_provider: str | None = None
        # Sprint 13.5: True when the *last* successful request fell back
        # from a failed higher-priority provider to a lower-priority one.
        # Exposed so provenance can mark fallback-served data (the
        # engine must never present a fallback source as primary data).
        self._fallback_used = False
        self._health_monitor = health_monitor
        # Centralized quality validator (Sprint 13.3 Phase 9).  Called as
        # ``data_validator(method, result)`` after a provider succeeds;
        # when it raises ``InvalidDataError`` the result is rejected and
        # the next provider in the chain is tried — a malformed payload
        # from one source can never silently become a trading signal.
        self._data_validator = data_validator

    @property
    def last_provider(self) -> str | None:
        """Name of the provider that served the last successful request."""
        return self._last_provider

    @property
    def fallback_used(self) -> bool:
        """True when the last successful request used a fallback source."""
        return self._fallback_used

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
        import time as _time  # noqa: PLC0415 - latency measurement

        errors: list[str] = []
        providers_to_try = self._get_healthy_providers()

        if not providers_to_try:
            # All providers disabled — try original list as fallback
            providers_to_try = list(self._providers)
            logger.warning("[Hybrid] All providers disabled; trying originals")

        from src.data.exceptions import InvalidDataError, ProviderTimeout  # noqa: PLC0415
        from src.data.incidents import (  # noqa: PLC0415 - bounded tracker
            INCIDENT_FALLBACK,
            INCIDENT_MALFORMED,
            INCIDENT_PROVIDER_TIMEOUT,
            INCIDENT_PROVIDER_UNAVAILABLE,
            incident_tracker,
        )
        from src.data.quality import quality_metrics  # noqa: PLC0415 - bounded counters

        # Sprint 13.6: the hybrid chain feeds the *shared* health monitor
        # per provider (not just the DataService-level "api" bucket) so
        # reliability history and degradation classification reflect the
        # real per-source outcomes.
        monitor = self._health_monitor
        symbol_hint = str(args[0]) if args else ""

        self._fallback_used = False
        for index, provider in enumerate(providers_to_try):
            start = _time.perf_counter()
            try:
                func = getattr(provider, method)
                result = func(*args, **kwargs)
                # Centralized validation: reject a payload that violates
                # the canonical market-data contract so the next
                # provider (or controlled unavailability) is used
                # instead of silently propagating bad data (Sprint 13.3).
                if self._data_validator is not None:
                    self._data_validator(method, result)
                self._last_provider = provider.name
                # Sprint 13.5: a success from any provider after the
                # first means a fallback served the request — record it
                # for provenance (``fallback_used``) so downstream
                # consumers can distinguish primary from fallback data.
                self._fallback_used = index > 0
                if monitor is not None:
                    monitor.record_success(
                        provider.name,
                        latency_ms=(_time.perf_counter() - start) * 1000.0,
                    )
                if index > 0:
                    incident_tracker.record(
                        INCIDENT_FALLBACK,
                        providers=[provider.name],
                        symbol=symbol_hint,
                        trust_consequence="fallback (lower-priority source served)",
                        fallback_used=True,
                    )
                logger.info("[Hybrid] %s succeeded via %s", method, provider.name)
                return result
            except InvalidDataError as exc:
                errors.append(f"[{provider.name}] {exc}")
                quality_metrics.record_provider_failure()
                quality_metrics.record_fallback()
                if monitor is not None:
                    monitor.record_malformed(provider.name)
                incident_tracker.record(
                    INCIDENT_MALFORMED,
                    providers=[provider.name],
                    symbol=symbol_hint,
                    trust_consequence="rejected by canonical validator",
                    detail=str(exc)[:200],
                )
                logger.warning(
                    "[Hybrid] %s rejected via %s (invalid data): %s",
                    method,
                    provider.name,
                    exc,
                )
                continue
            except ProviderTimeout as exc:
                errors.append(f"[{provider.name}] {exc}")
                quality_metrics.record_provider_failure()
                quality_metrics.record_provider_timeout()
                quality_metrics.record_fallback()
                if monitor is not None:
                    monitor.record_timeout(provider.name)
                incident_tracker.record(
                    INCIDENT_PROVIDER_TIMEOUT,
                    providers=[provider.name],
                    symbol=symbol_hint,
                    trust_consequence="fallback attempted",
                )
                logger.warning("[Hybrid] %s timed out via %s: %s", method, provider.name, exc)
                continue
            except Exception as exc:
                errors.append(f"[{provider.name}] {exc}")
                quality_metrics.record_provider_failure()
                quality_metrics.record_fallback()
                if monitor is not None:
                    monitor.record_failure(provider.name)
                # Per-provider generic failure: recorded as a fallback
                # attempt when a later provider may still serve.  The
                # single UNAVAILABLE incident is recorded once after the
                # loop when *every* provider failed (no duplication).
                if index < len(providers_to_try) - 1:
                    incident_tracker.record(
                        INCIDENT_FALLBACK,
                        providers=[provider.name],
                        symbol=symbol_hint,
                        trust_consequence="fallback attempted",
                        detail=str(exc)[:200],
                    )
                logger.warning("[Hybrid] %s failed via %s: %s", method, provider.name, exc)
                continue
        # All providers failed: record the aggregated outage incident
        # exactly once (bounded; kind is stable).
        incident_tracker.record(
            INCIDENT_PROVIDER_UNAVAILABLE,
            providers=[p.name for p in providers_to_try],
            symbol=symbol_hint,
            trust_consequence="no trusted data",
            detail=f"all providers failed for {method}",
        )
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

    def get_reconciled_history(
        self,
        symbol: str,
        days: int = 365,
        *,
        preferred_source: str | None = None,
    ) -> tuple[pd.DataFrame, Any]:
        """Query **every** healthy provider and reconcile their results.

        Sprint 13.4 (Phase 16): unlike ``get_history`` (fail-fast single
        best provider), this collects *all* successful provider frames
        and reconciles them with the canonical reconciliation layer.
        The default ``get_history`` fallback chain is untouched — this is
        an additive path used by ``DataService.get_reconciled_history``.

        Policy: provider priority is preserved as the tie-break for
        MINOR disagreements (never averaged); a MATERIAL disagreement
        drops the conflicting date(s); when no provider succeeds,
        ``DataUnavailable`` is raised (same as the fail-fast chain).

        Returns:
            ``(frame, ReconciliationResult)`` — the trusted merged frame
            and the history-wide reconciliation outcome.
        """
        from src.data.reconciliation import (  # noqa: PLC0415 - lazy
            reconcile_history,
            reconciliation_metrics,
        )

        frames: dict[str, pd.DataFrame | None] = {}
        errors: list[str] = []
        # Sprint 13.5: fallback observability parity — when any provider
        # fails but at least one succeeds, the served data is a fallback
        # result (lower-priority source) and provenance must record it.
        any_failure = False
        for provider in self._get_healthy_providers():
            try:
                reconciliation_metrics.record_provider_request()
                df = provider.get_history(symbol, days)
                if self._data_validator is not None:
                    self._data_validator("get_history", df)
                frames[provider.name] = df
                reconciliation_metrics.record_provider_success()
            except InvalidDataError as exc:
                errors.append(f"[{provider.name}] {exc}")
                any_failure = True
                reconciliation_metrics.record_provider_failure()
            except ProviderTimeout as exc:
                errors.append(f"[{provider.name}] {exc}")
                any_failure = True
                reconciliation_metrics.record_provider_timeout()
                reconciliation_metrics.record_provider_failure()
            except Exception as exc:  # noqa: BLE001 - per-provider isolation
                errors.append(f"[{provider.name}] {exc}")
                any_failure = True
                reconciliation_metrics.record_provider_failure()

        if not any(f is not None for f in frames.values()):
            raise DataUnavailable(
                f"All providers failed to reconcile {symbol}: {'; '.join(errors)}"
            )

        merged, result = reconcile_history(
            frames,
            symbol=symbol,
            preferred_source=preferred_source,
        )
        reconciliation_metrics.record_reconciliation(result)
        # Sprint 13.6: bounded incident records for reconciliation
        # outcomes (material / mapping / repeated-minor / per-provider
        # failures) so operators can answer "why was this suppressed".
        from src.data.incidents import (  # noqa: PLC0415 - bounded tracker
            INCIDENT_MAPPING_CONFLICT,
            INCIDENT_MATERIAL_DISAGREEMENT,
            INCIDENT_PROVIDER_TIMEOUT,
            INCIDENT_PROVIDER_UNAVAILABLE,
            incident_tracker,
        )

        if result.status in ("MATERIAL_DISAGREEMENT", "MAPPING_CONFLICT"):
            kind = (
                INCIDENT_MAPPING_CONFLICT
                if result.status == "MAPPING_CONFLICT"
                else INCIDENT_MATERIAL_DISAGREEMENT
            )
            incident_tracker.record(
                kind,
                providers=list(result.providers),
                symbol=symbol,
                trust_consequence="conflicted (signal suppressed to HOLD)",
                quarantined=result.status == "MATERIAL_DISAGREEMENT",
                detail="; ".join(result.warnings)[:200],
            )
            # A MAPPING_CONFLICT caused by an identifier/company-identity
            # mismatch is additionally observable as an identity-mismatch
            # incident (Sprint 13.6 §7) — the two kinds are not mutually
            # exclusive: the reconciliation-level kind says *what* was
            # suppressed, the identity kind says *why* (the providers
            # disagreed about which security the record belongs to).
            if result.status == "MAPPING_CONFLICT" and any(
                "identifier" in w.lower() or "identity" in w.lower()
                for w in result.warnings
            ):
                from src.data.incidents import (  # noqa: PLC0415 - bounded
                    INCIDENT_IDENTITY_MISMATCH,
                )

                incident_tracker.record(
                    INCIDENT_IDENTITY_MISMATCH,
                    providers=list(result.providers),
                    symbol=symbol,
                    trust_consequence="conflicted (signal suppressed to HOLD)",
                    detail="; ".join(result.warnings)[:200],
                )
        elif result.status == "MINOR_DISAGREEMENT":
            incident_tracker.record_minor(
                symbol, providers=list(result.providers)
            )
        elif result.status == "UNAVAILABLE":
            incident_tracker.record(
                INCIDENT_PROVIDER_UNAVAILABLE,
                providers=[p for p, f in frames.items() if f is not None]
                or [p.name for p in self._providers],
                symbol=symbol,
                trust_consequence="no trusted data",
            )
        self._last_provider = result.selected_source or next(
            (p for p, f in frames.items() if f is not None), None
        )
        # Sprint 13.5: mark the reconciled result as fallback-served when
        # any provider in the chain failed (the merged frame came from
        # fewer sources than configured) — provenance parity with the
        # fail-fast ``_try_all`` path.
        result.fallback_used = bool(any_failure) and any(
            f is not None for f in frames.values()
        )
        return merged, result

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
