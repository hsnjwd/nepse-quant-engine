# Changelog

All notable changes to the NEPSE Quant Engine project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v1.0.0-rc2] — 2026-08-05

### Added
- **Canonical version module** (`src/version.py`) — single version source read from the root `VERSION` file; FastAPI metadata and the `src` package now report the same version
- **CSV path resolver** (`src/loaders/csv_loader.py::resolve_stock_csv_path`) — single authoritative symbol→CSV-path helper used by `api/analyze.py`, `api/backtest.py` and `watchlist/scanner.py`
- **Sprint 10 regression suite** (`tests/test_sprint10.py`) — momentum MA filter, confidence consolidation, single CSV loader, config and version consistency

### Changed
- **Momentum strategy fix (critical)**: `MomentumStrategy` now reads the engine's real indicator columns `SMA_20`/`SMA_50` instead of the non-existent `MA20`/`MA50`. The old silent `close` fallback disabled the moving-average trend filter entirely (BUY fired on RSI alone). Missing indicator columns now raise a clear `ValueError` instead of silently degrading
- **Confidence engine consolidation**: `src/decision/confidence.py` is the single implementation; legacy `src/decision/engine.py` is deprecated (kept working, emits `DeprecationWarning`, documents migration to `confidence.py` / `recommendations.trade_plan.py`)
- **Single CSV parser**: `CSVProvider._load_csv` now delegates to `src/loaders/csv_loader.py::load_csv` — one CSV parsing implementation for the whole platform
- **Configuration consolidation**: `DataService._default_api_urls` now derives from `src.config.DATA_SERVICE_API_URLS` (added the previously-missing `github_datasets` key); Telegram bot reads `API_BASE_URL`/`TELEGRAM_TOKEN` from `src.config` instead of re-reading env vars
- **Exception handling**: replaced redundant `except (NotImplementedError, Exception)` tuples, added logging to silent `return []` paths in `DataService` top-movers and provider request failures

### Removed / Deprecated
- **10 legacy ad-hoc smoke scripts** under `src/` (`src/test_decision.py`, `src/test_indicators.py`, `src/test_launcher_utils.py`, `src/test_market_scanner.py`, `src/test_momentum.py`, `src/test_phase54.py`, `src/test_pipeline.py`, `src/test_signal.py`, `src/test_volatility.py`, `src/test_volume.py`) — marked deprecated (broken flat imports; the real suite lives in `tests/`). TODO(v1.1): delete
- Duplicate module docstring in `src/__init__.py`

---

## [v1.0.0-rc1] — 2026-07-30

### Added

#### Streamlit Frontend (24 Pages)
- **Dashboard** — Live NEPSE index, top gainers/losers/turnover, market breadth, portfolio value, watchlist signals, recent trades, alerts feed
- **Market Scanner** — Filter by signal, score, confidence, RSI, volume, price, regime; saved presets, export CSV, pinned symbols
- **Stock Analysis** — Deep technical analysis: candlestick, volume, RSI, MACD, Bollinger Bands, ATR, OBV charts; support/resistance levels
- **Advanced Charts** — Real-time WebSocket updates, live candlesticks, live indicators, LIVE/DISCONNECTED status indicator
- **Watchlist** — Live price and signal updates every 30s; add/remove/scan
- **Portfolio** — SQLite-backed persistent portfolio: cash, holdings, transactions, P&L, allocation pie chart, performance
- **Portfolio Analytics** — Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor, rolling returns/Sharpe/volatility
- **Paper Trading** — Market/limit/stop-loss/take-profit orders, pending queue, trade history, open positions, balance tracking
- **Market Regime** — Detected regime with confidence, reasons, metrics (ADX, ATR, RSI, MACD, trend, drawdown, volume)
- **Backtesting** — Select strategy, stocks, date range, parameters; equity curve, drawdown, trades, metrics
- **Optimizer** — Parameter optimisation with heatmap and ranking
- **Alerts Center** — Price, volume, RSI, MACD cross, breakout, regime change, portfolio alerts; read/unread, dismiss, priorities
- **Reports** — Generate CSV, Excel, JSON, HTML, PDF reports for portfolio, backtests, market scan, watchlist
- **Cache & Performance Debug** — Memory/disk cache inspection, metrics, provider health, WebSocket status, background refresh, rate limiter
- **System Status** — Cache status, provider health, API latency, WebSocket, background refresh, memory/CPU, request metrics
- **Notifications** — Unified drawer with unread badge, categories (portfolio, scanner, price, market, WebSocket, system), search, filter
- **Settings** — Theme (Dark/Light/TradingView/Bloomberg), refresh interval, cache TTL, API provider priority, notifications, chart preferences
- **Keyboard Shortcuts** — Ctrl+K/R/P/S/B/D/H/? — global navigation and help

#### DataService (Centralised Data Layer)
- **DataService Singleton** — Single entry point for all market data across the entire application
- **TieredCache** — In-memory (fast) + disk (persistent) with configurable TTL expiry
- **HybridProvider** — Automatic fallback: API → CSV → cache → graceful empty
- **APIProvider** — Configurable REST endpoints with requests/httpx fallback, timeout, error handling
- **CSVProvider** — Automatic discovery of `data/history/*.csv`, `data/stocks/*.csv`, `data/*.csv`
- **RateLimiter** — Token-bucket per provider, exponential backoff with jitter, 429/503 handling
- **ProviderHealthMonitor** — Auto-disable providers after 5 consecutive failures, re-enable after recovery period
- **MetricsCollector** — Total requests, cache hit rate, P50/P95/P99 latency, per-operation and per-provider breakdown
- **LiveMarketStream** — WebSocket with auto-reconnect, heartbeat, subscriber pattern, fallback to polling
- **Background Refresh** — Thread-safe, pause/resume, dynamic interval, graceful shutdown, automatic exception recovery

#### Portfolio & Trading
- **Portfolio Database** — SQLite-backed persistent storage: holdings, transactions, realised/unrealised P&L
- **Portfolio Analytics** — Daily/weekly/monthly/YTD returns, CAGR, Sharpe/Sortino/Calmar, Alpha/Beta, Information/Treynor ratios
- **Paper Trading Engine** — Market/limit/stop-loss/take-profit orders, pending queue, partial fills, commission, trade history
- **Trade Journal** — Automatic logging, notes, screenshots, lessons learned, tags, strategy, confidence, emotion, R multiple
- **Market Replay** — Play/pause/step/fast-forward/rewind through historical sessions; cache injection for live UI compatibility

#### Security & Deployment
- **Security Audit** — Removed traceback leaks (`st.exception`), added CORS middleware (configurable origins), environment variable validation
- **Docker Support** — Multi-stage Dockerfile (web/api/bot targets), docker-compose.yml with health checks
- **CI/CD** — GitHub Actions: test suite + Docker build + multi-arch release pipeline
- **Backup/Restore** — Linux shell scripts and Windows batch scripts with 30-day retention
- **Launcher Scripts** — Linux `run_app.sh` and Windows `run_app.bat` with mode selection (web/api/bot/all)
- **Production Config** — `src/config/production.py` with safe defaults (longer TTLs, conservative rate limits)

### Changed
- **Market Regime Detector**: Added PANIC regime detection, refined DISTRIBUTION and RECOVERY thresholds, updated regime decision order (PANIC → OVERHEATED → BULL → BEAR → RECOVERY → DISTRIBUTION → ACCUMULATION → SIDEWAYS → LOW_VOLATILITY → HIGH_VOLATILITY)
- **Architecture Migration**: All Streamlit pages migrated from scattered `requests.get()` / `pd.read_csv()` calls to centralised DataService
- **Code Quality**: Removed dead code, unused imports, oversized functions; added comprehensive type hints, docstrings, and logging
- **Performance Optimisation**: LRU-cached Plotly figures (`lru_cache`), `@st.cache_resource` for DataService, lazy imports, background data preloading
- **PDF Export**: Added ReportLab-based PDF generation with cover page, tables, charts, page numbers, timestamps

### Fixed
- **Sprint 7A Stability**: Fixed DataService singleton attribute initialization order, removed all `hasattr()` workarounds, fixed provider contract completeness
- **Sprint 7B Test Failures**: Fixed `TopMovers` import error (was referenced before import), fixed `colorscale` property error on Scatter trace in optimisation chart
- **Sprint 7F Security**: Replaced `st.exception(e)` traceback leak with logged error and user-friendly message; added `CORSMiddleware` with configurable origins
- **Sprint 7D Caching**: Fixed `TypeError: unhashable type: 'DataFrame'` in `_build_candlestick` by extracting OHLCV tuples before cache entry

### Performance
- **Dashboard Load**: < 500ms initial load (pre-cached via background refresh)
- **Cached Quotes**: < 20ms (in-memory cache hit)
- **History Load**: < 100ms (tiered cache with disk fallback)
- **Chart Rendering**: ~0ms for repeated inputs (LRU-cached Plotly figures)
- **Cache Hit Rate**: Typically > 80% during active usage

---

## [v0.6.5] — 2026-07-29

### Added
- Complete Streamlit frontend with 24 pages
- DataService singleton with tiered caching
- Hybrid API/CSV/fallback provider chain
- WebSocket live feed infrastructure
- Provider health monitoring and rate limiting
- Portfolio database (SQLite) and analytics
- Paper trading engine (market/limit/stop-loss/take-profit)
- Market replay engine with cache injection
- Alert center with notification drawer
- Theme manager (Dark/Light/TradingView/Bloomberg)
- Keyboard shortcuts (Ctrl+K/R/P/S/B/D/H/?)
- Export center (CSV/Excel/JSON/HTML/PDF)
- Docker deployment (multi-stage, docker-compose)
- CI/CD pipeline (GitHub Actions, GHCR)
- Backup/restore scripts (Linux + Windows)
- Performance debug dashboard
- System status page

### Fixed
- Import errors across Streamlit pages
- Singleton lifecycle management
- Background refresh thread safety
- Provider fallback edge cases
- File encoding issues (utf-8 vs cp1252)
- Plotly version compatibility

---

## [v0.5.1] — 2026-07-25

### Added
- **Backtest Engine Completed**: Finished orchestration layer in `src/backtest/engine.py` connecting CSV data loading, signal evaluation, trade simulation, analytics calculations, and report generation.
- **Backtest Metrics**: Implemented pure quantitative performance metrics in `src/backtest/metrics.py` including win rate, profit factor, average win/loss, expectancy, max drawdown, Sharpe ratio, and CAGR.
- **Report Generator**: Added `src/backtest/report.py` to format trade execution histories and performance statistics into structured summary dictionaries and human-readable text overviews.
- **BACKTESTING.md Documentation**: Created comprehensive technical architecture and usage documentation in `docs/BACKTESTING.md`.
- **55 Automated Tests**: Expanded test suite to 55 comprehensive unit and integration tests with 100% pass rate across engine, simulator, metrics, report, API, and strategy modules.

### Changed & Improved
- **Trade Simulator Refactor**: Refactored `src/backtest/trade_simulator.py` into a professional trade execution engine featuring `ExitReason` and `TradeResult` Enums, `ExecutionConfig` dataclass, normalized `TradeRecord` modeling, position sizing (`shares`), and adverse slippage/commission calculations.
- **API Validation Improvements**: Enhanced input validation across all FastAPI endpoints in `src/api/` (`analyze`, `backtest`, `watchlist`), blocking path traversal attempts (`..`, `/`, `\\`) and bad parameter inputs with appropriate `400 Bad Request`, `404 Not Found`, and `500 Internal Server Error` HTTP status codes.
- **Ranking Engine Improvements**: Refined signal scoring, indicator weighting, and candidate ranking calculations in indicator and scoring modules.
- **Scanner Integration**: Enhanced market scanner services and endpoints (`/market/`, `/market/top10`, `/market/buylist`, `/market/selllist`, `/market/strongbuy`) for real-time candidate screening.
- **Logging Improvements**: Replaced console `print()` statements across API and engine modules with structured `logger` calls (`logger.info()`, `logger.debug()`, `logger.error()`).
- **Documentation Updates**: Updated module docstrings, type annotations, and system architecture docs.

---

## [v0.5.0] — 2026-07-20

### Added
- Portfolio analyzer module (`src/portfolio/analyzer.py`) and advice generator (`src/portfolio/advisor.py`).
- Market scanner service (`src/services/market_service.py`) for automated screening.
- Watchlist manager and scanner (`src/watchlist/manager.py`, `src/watchlist/scanner.py`).

### Changed
- Refactored REST API routers under `src/api/`.

---

## [v0.4.0] — 2026-07-10

### Added
- Core technical analysis engine (`src/engine/analyzer.py`).
- Indicator calculation libraries for Moving Averages, RSI, MACD, Volume, and Volatility (`src/indicators/`).
- Candlestick pattern detection algorithms (`src/patterns/candlestick.py`).
- Signal scoring engine (`src/signals/scorer.py`).
- Market regime detector (`src/regime/detector.py`) with PANIC, OVERHEATED, BULL, BEAR, RECOVERY, DISTRIBUTION, ACCUMULATION, SIDEWAYS regimes.
- Adaptive strategy engine (`src/adaptive/engine.py`).
- Risk management (`src/risk/`): position sizing, reward calculation, engine.
- Decision engine (`src/decision/`): confidence, market filter, risk, signal, support/resistance.
- Monte Carlo simulation (`src/simulation/monte_carlo.py`).
- Parameter optimisation (`src/optimization/parameter_optimizer.py`) and walk-forward analysis.
- Portfolio optimiser (`src/portfolio/optimizer.py`).
- Trade recommendation engine (`src/recommendations/`, including trade plan).
- FastAPI REST endpoints (`src/api/`): analyze, backtest, portfolio, regime, scanner, simulation, watchlist.
- Telegram bot (`src/bot/telegram_bot.py`) for optional monitoring.
- Dashboard charts module (`src/dashboard/charts.py`) and metrics.
- Data loaders for CSV (`src/loaders/csv_loader.py`).
- Market cache (`src/cache/market_cache.py`).
- Data validator (`src/validators/data_validator.py`).
- Comprehensive test suite with 800+ tests.

### Changed
- Project restructuring into modular `src/` package layout.
- Migration from flat scripts to clean architecture.
