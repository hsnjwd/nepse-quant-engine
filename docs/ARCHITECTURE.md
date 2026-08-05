# NEPSE Quant Engine — Architecture (v1.0)

## Sprint 10 — Architecture Consolidation

Sprint 10 established **single sources of truth** across the platform:

| Concern | Single source of truth | Notes |
|---------|------------------------|-------|
| CSV parsing | `src/loaders/csv_loader.py::load_csv` | `CSVProvider._load_csv` delegates here; `analyzer`, `backtest.engine` and the API all route through it |
| CSV path resolution | `src/loaders/csv_loader.py::resolve_stock_csv_path` | Replaces inline `Path(DATA_DIRECTORY)/symbol.csv` logic in `api/analyze.py`, `api/backtest.py`, `watchlist/scanner.py` |
| Confidence engine | `src/decision/confidence.py::calculate_confidence` | Legacy `src/decision/engine.py` is deprecated (backward-compatible, warns) |
| Configuration | `src/config.py` | `DataService._default_api_urls` is derived from `DATA_SERVICE_API_URLS` (5 keys incl. `github_datasets`) |
| Version | `src/version.py` (reads root `VERSION` file) | Used by `src.api.main`, `src/__init__` |

### Canonical data flow

```
CSV/API
   │
   ▼
Providers (src/data/providers.py)     CSV parsing ONLY via src/loaders/csv_loader.load_csv
   │
   ▼
DataService (src/data/service.py)     single entry point, tiered cache, health monitor
   │
   ├─► Indicators (src/indicators/)    SMA_20/SMA_50, RSI, MACD, ATR, BB …
   │
   ├─► Strategies (src/strategies/)    MomentumStrategy reads SMA_20/SMA_50 (no silent fallback)
   │
   ├─► Signal Engine (src/signals/)    scoring → confidence (src/decision/confidence.py)
   │
   ├─► Decision Engine (src/decision/) single confidence implementation
   │
   ├─► Portfolio / Alerts / API / Telegram Bot / UI
```

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Streamlit Frontend                          │
│                       (app.py + 24 page modules)                    │
│                                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │Dashboard│ │ Scanner  │ │ Analysis │ │Portfolio│ │ Settings  │  │
│  └────┬────┘ └────┬─────┘ └────┬─────┘ └────┬───┘ └─────┬─────┘  │
│       │           │            │            │            │        │
│  ┌────┴───────────┴────────────┴────────────┴────────────┴─────┐  │
│  │               UI Components / Helpers / Theme                │  │
│  │    (src.ui.components, src.ui.helpers, src.ui.theme)         │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                     │
└──────────────────────────────┼─────────────────────────────────────┘
                               │
┌──────────────────────────────┼─────────────────────────────────────┐
│                  DataService (src.data.service)                     │
│                     (Singleton, Background Refresh)                  │
│                                                                     │
│  ┌──────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │Tiered    │ │  Hybrid      │ │ Metrics  │ │ LiveMarketStream  │  │
│  │Cache     │ │  Provider    │ │Collector │ │ (WebSocket)       │  │
│  └────┬─────┘ └──────┬───────┘ └──────────┘ └────────┬─────────┘  │
│       │              │                                │            │
│  ┌────┴─────┐   ┌────┴───────┐               ┌───────┴────────┐  │
│  │Memory    │   │ API       │               │  Auto-Reconnect │  │
│  │Cache     │   │ Provider  │               │  + Heartbeat    │  │
│  ├──────────┤   ├───────────┤               │  + Subscribers  │  │
│  │Disk      │   │ CSV       │               └─────────────────┘  │
│  │Cache     │   │ Provider  │                                     │
│  └──────────┘   └────┬───────┘                                     │
│                      │                                             │
│  ┌──────────┐   ┌────┴───────┐   ┌──────────┐   ┌──────────────┐  │
│  │  Rate    │   │  Health    │   │  Live    │   │  Exceptions  │  │
│  │ Limiter  │   │  Monitor   │   │  Data    │   │  Hierarchy   │  │
│  └──────────┘   └────────────┘   └──────────┘   └──────────────┘  │
│                                                                     │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────┼─────────────────────────────────────┐
│                      Backend Modules                               │
│                                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │ Engine  │ │ Portfolio│ │ Paper    │ │ Backtest│ │ Replay    │  │
│  │(analyzer)│ │ Database │ │ Trading  │ │ Engine  │ │ Engine    │  │
│  └────┬────┘ └──────────┘ └──────────┘ └────┬────┘ └─────┬─────┘  │
│       │                                     │            │        │
│  ┌────┴────┐  ┌──────────┐ ┌──────────┐ ┌──┴─────┐ ┌───┴──────┐  │
│  │Indicators│  │ Regime  │ │ Signals  │ │Scanner │ │ Trading  │  │
│  │(MA, RSI, │  │ Detector│ │ Scorer   │ │ Engine │ │ Journal  │  │
│  │ MACD...) │  └──────────┘ └──────────┘ └────────┘ └──────────┘  │
│  └──────────┘                                                      │
│                                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │Strategies│ │ Risk     │ │ Decision │ │ Alerts │ │ Watchlist  │  │
│  │(momentum,│ │ Engine   │ │ Engine   │ │ Center │ │ Manager    │  │
│  │breakout) │ └──────────┘ └──────────┘ └────────┘ └───────────┘  │
│  └──────────┘                                                      │
│                                                                     │
|  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │FastAPI   │ │ Monte    │ │ Parameter│ │ Walk   │ │ Export    │  │
│  │REST API  │ │ Carlo    │ │ Optimiser│ │ Forward│ │ Center    │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ └───────────┘  │
│                                                                     │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
               ┌────▼────┐          ┌────▼────┐
               │NEPSE API│          │Local CSV│
               │Sources  │          │ Data    │
               └─────────┘          └─────────┘
```

## Data Flow

```
User Action                     Streamlit Page
      │                              │
      ▼                              ▼
   UI Event ────────────────► DataService.get_*()
                                    │
                          ┌─────────┴──────────┐
                          ▼                    ▼
                    TieredCache           HybridProvider
                          │                    │
                    ┌─────┴─────┐        ┌─────┴──────┐
                    ▼           ▼        ▼            ▼
              Memory Cache  Disk Cache  API      CSV Provider
                                         │            │
                                    ┌────┴────┐  ┌────┴────┐
                                    │  Rate   │  │ Auto-   │
                                    │ Limiter │  │ Discover│
                                    └─────────┘  └─────────┘
                                         │
                                    ┌────┴────┐
                                    │  Health │
                                    │ Monitor │
                                    └─────────┘
```

## Module Dependency Graph

```
src.ui.pages.*
    ↓
src.ui.components / src.ui.helpers / src.ui.theme
    ↓
src.data.service.DataService
    ↓
├── src.data.cache.TieredCache
│   ├── MemoryCache
│   └── DiskCache
├── src.data.providers.HybridProvider
│   ├── APIProvider (→ requests/httpx → NEPSE API)
│   └── CSVProvider (→ pd.read_csv → local files)
├── src.data.websocket.LiveMarketStream
├── src.data.metrics.MetricsCollector
├── src.data.health.ProviderHealthMonitor
├── src.data.rate_limiter.RateLimiter
├── src.data.export.ExportCenter
│
├── src.portfolio.database.PortfolioDatabase
├── src.paper_trading.engine.PaperTradingEngine
├── src.replay.engine.MarketReplayEngine
├── src.alerts.center.AlertCenter
├── src.trading.journal.TradeJournal
│
├── src.engine.analyzer
├── src.regime.detector
├── src.signals.scorer
├── src.scanner.engine
├── src.strategies.*
├── src.risk.*
├── src.decision.*
│
├── src.api.main (FastAPI)
└── src.bot.telegram_bot (optional)
```

## Request Lifecycle

### Cache Hit Path

1. Page calls `svc.get_market_summary()`
2. DataService checks TieredCache for key `market_summary`
3. Memory cache hit → return immediately (< 1ms)
4. Disk cache hit → promote to memory → return (< 5ms)

### Cache Miss Path

1. Page calls `svc.get_market_summary()`
2. DataService checks TieredCache → miss
3. DataService calls HybridProvider
4. HybridProvider queries health monitor for best provider
5. API request → rate limiter acquires token → HTTP GET
6. Response parsed into MarketSummary dataclass
7. Stored in TieredCache (memory + disk)
8. Returned to page (< 500ms)

### Fallback Path

1. APIProvider raises TimeoutError
2. HybridProvider catches, logs, moves to next provider
3. CSVProvider attempts to load from data/*.csv
4. If CSV succeeds → parse → cache → return
5. If CSV fails → return MarketSummary.empty()
6. DataService never raises — empty default returned

## Provider Selection Algorithm

```
_input_: list of providers, health monitor
_output_: provider to use

1. healthy = [p for p in providers if health_monitor.is_healthy(p.name)]
2. if healthy is empty:
3.     use original list (override disabled state)
4. else:
5.     sort healthy by success_rate descending
6.     use first (healthiest) provider
7. try provider._do_*(...)
8. if fails: log warning, move to next
9. if all fail: return empty() default
```

## Cache Key Structure

| Key Pattern | Example | TTL |
|-------------|---------|-----|
| `market_summary` | `market_summary` | 30s |
| `market_status` | `market_status` | 30s |
| `top_gainers:{limit}` | `top_gainers:5` | 30s |
| `top_losers:{limit}` | `top_losers:5` | 30s |
| `top_turnover:{limit}` | `top_turnover:5` | 30s |
| `stock:{symbol}` | `stock:NABIL` | 30s |
| `history:{symbol}:{days}` | `history:NABIL:365` | 300s |
| `nepse_index:{days}` | `nepse_index:500` | 300s |
| `scan:{cache_key}` | `scan:...` | 30s |
| `watchlist` | `watchlist` | 30s |

## WebSocket Flow

```
LiveMarketStream
    │
    ├── connect(url)
    │       │
    │       ├── on_open: start heartbeat timer (30s)
    │       ├── on_message: parse JSON → dispatch subscribers
    │       ├── on_error: log, schedule reconnect
    │       └── on_close: schedule reconnect
    │
    ├── subscribe(callback)
    │       └── add to subscriber list
    │
    ├── unsubscribe(callback)
    │       └── remove from subscriber list
    │
    └── disconnect()
            └── stop heartbeat, close socket, cancel reconnect

Reconnect: exponential backoff (1s → 2s → 4s → ... → 60s max)
Fallback: DataService polls API at configured interval
```

## Configuration

All settings in `src/config.py`, overridable via environment variables or `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBSOCKET_ENABLED` | `False` | Enable WebSocket live feed |
| `WEBSOCKET_URL` | `""` | WebSocket server URL |
| `RATE_LIMIT` | `10` | Max tokens per provider |
| `API_TIMEOUT` | `15` | HTTP request timeout (seconds) |
| `RETRY_COUNT` | `3` | Max retries on failure |
| `BACKOFF_BASE` | `1.0` | Initial backoff (seconds) |
| `CACHE_MEMORY_TTL` | `60` | Memory cache TTL (seconds) |
| `CACHE_DISK_TTL` | `300` | Disk cache TTL (seconds) |
| `CACHE_REFRESH_INTERVAL` | `60` | Background refresh interval (seconds) |
| `ENABLE_PERFORMANCE_MONITORING` | `False` | Enable metrics collection |
| `LOG_LEVEL` | `INFO` | Logging level |
| `CORS_ORIGINS` | `http://localhost:8501` | Allowed CORS origins (comma-separated) |
| `NEPSE_API_BASE_URL` | `""` | Custom NEPSE API base URL |

## Project Structure

```
src/
├── alerts/           Alert rules, notification centre
├── api/              FastAPI REST endpoints
├── backtest/         Backtest engine, trade simulator, metrics
├── bot/              Optional Telegram client
├── config/           Production config, user settings
├── data/             DataService, providers, cache, WebSocket, metrics, health
├── dashboard/        Dashboard charts, reports, exporter
├── decision/         Signal engine, confidence, risk
├── engine/           Core analysis pipeline
├── indicators/       Technical indicators
├── logging/          Centralised logging
├── market_structure/ Support/resistance, levels
├── optimization/     Parameter optimiser, walk-forward
├── paper_trading/    Paper trading engine
├── portfolio/        Portfolio database, analytics
├── regime/           Market regime detector
├── replay/           Market replay engine
├── recommendations/  Trade recommendations
├── risk/             Risk management
├── scanner/          Market scanner, ranking
├── services/         Market service layer
├── signals/          Signal scoring
├── strategies/       Trading strategies
├── trading/          Trade journal
├── ui/               Streamlit frontend (pages, components, theme)
├── validators/       Data validation
└── watchlist/        Watchlist management
```
