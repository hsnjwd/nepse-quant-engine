# Known Issues — v1.0.0-rc1

This document lists known issues, limitations, and workarounds for the NEPSE Quant Engine v1.0.0-rc1 release.

---

## High Priority

### 1. Live API Endpoints May Be Unavailable

**Issue:** The NEPSE API endpoints configured in `src/config.py` may return 404 errors if the upstream APIs change their URLs or are temporarily unavailable.

**Impact:** Dashboard, scanner, watchlist, and stock analysis pages will show cached or empty data.

**Workaround:** The DataService will automatically fall back to CSV data and cached data. Configure custom API URLs in `.env`:
```env
NEPSE_API_BASE_URL=https://your-api-endpoint/api/v1
```

**Status:** Ongoing — API providers are external and may change without notice.

### 2. WebSocket Server Not Deployed

**Issue:** The `LiveMarketStream` infrastructure is implemented and tested, but no production WebSocket server endpoint is configured by default.

**Impact:** The "LIVE" indicator on Advanced Charts will show "DISCONNECTED". Data will be fetched via polling instead.

**Workaround:** Configure a WebSocket URL:
```env
WEBSOCKET_URL=wss://your-websocket-server/ws
WEBSOCKET_ENABLED=true
```

**Status:** Requires a WebSocket-compatible market data server deployment.

### 3. Paper Trading Is In-Memory

**Issue:** The `PaperTradingEngine` stores orders, positions, and trades in memory. Restarting the app loses all paper trading state.

**Impact:** Active paper trades do not persist across application restarts.

**Workaround:** None currently. The Portfolio Database (SQLite) is persistent, but paper trading is not yet backed by it.

**Status:** Planned for Sprint 7H.

---

## Medium Priority

### 4. Alert Persistence

**Issue:** Alerts are stored in memory and cleared on app restart.

**Impact:** Alert history is lost after restart.

**Workaround:** None.

**Status:** Planned for v1.1.0.

### 5. No Multi-User Support

**Issue:** The application is designed for single-user use. Streamlit's session state and SQLite database are not designed for concurrent multi-user access.

**Impact:** Multiple users accessing the same instance will share portfolio, settings, and watchlist state.

**Workaround:** Run separate instances for each user (Docker Compose with separate volumes).

**Status:** Not planned for v1.x.

### 6. Limited Market Data History

**Issue:** CSV data files are not bundled with the repository. Users must obtain NEPSE historical data separately.

**Impact:** Backtesting and chart history will be limited to data available in `data/` directory.

**Workaround:** Place CSV files in `data/history/` directory following the format `SYMBOL.csv` with columns: Date, Open, High, Low, Close, Volume.

**Status:** Documentation to be improved for data acquisition.

---

## Low Priority

### 7. Dashboard Render Time on First Load

**Issue:** The first page load may take 2-5 seconds while background refresh downloads market data.

**Impact:** Initial dashboard render is slower than subsequent renders.

**Workaround:** None needed — data is cached after first load. All subsequent renders are < 500ms.

### 8. No Offline Mode

**Issue:** The application requires network connectivity for live data.

**Impact:** The UI will display cached or empty data when offline.

**Workaround:** Ensure `data/history/*.csv` files are available for fallback.

### 9. Limited Test Coverage for UI Pages

**Issue:** Page render functions cannot be tested without Streamlit runtime.

**Impact:** 24 Streamlit pages are not covered by pytest.

**Workaround:** Manual verification via `python -m streamlit run app.py`.

---

## Deprecation Notice

- `src/data/live_data.py` — Deprecated in favour of `DataService`. Will be removed in v2.0.0.
- `src/data/cache.py` standalone module — Replaced by `TieredCache` in `DataService`.
- Direct `requests.get()` / `pd.read_csv()` in UI pages — All such usages have been migrated to `DataService`.
