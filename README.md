# NEPSE Quant Engine

[![CI](https://github.com/hsnjwd/nepse-quant-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/hsnjwd/nepse-quant-engine/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ready-blue)](https://github.com/hsnjwd/nepse-quant-engine/pkgs/container/nepse-quant-engine)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Open-source quantitative trading and market analysis platform** for the Nepal Stock Exchange (NEPSE).

NEPSE Quant Engine is a research-oriented Python platform for analyzing listed securities, computing technical indicators, generating trading signals, simulating trade executions (backtesting), computing quantitative performance metrics, and managing a live portfolio — all accessible through a **professional Streamlit dashboard** and a **FastAPI REST API**.

> This project is not a Telegram bot. It includes an optional Telegram client for monitoring and notifications, but the core system is the analysis, backtesting, signal-generation, and portfolio-management framework.

---

## 🚀 Quick Start

### Docker (recommended)

```bash
git clone https://github.com/hsnjwd/nepse-quant-engine.git
cd nepse-quant-engine
cp .env.example .env
docker compose up -d web
# Open http://localhost:8501
```

### Native Python

```bash
pip install -r requirements.txt
python -m streamlit run app.py
# Open http://localhost:8501
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for full deployment options (Linux systemd, Windows, Docker Compose with API/Bot services, backup/restore).

---

## Features

### Streamlit Dashboard
Professional trading terminal with live NEPSE market data, interactive charts, and real-time portfolio tracking.

- **Dashboard**: Market snapshot, top gainers/losers, market breadth, signals, portfolio summary
- **Scanner**: Filter by signal, score, confidence, RSI, price — with saved presets and export
- **Stock Analysis**: Deep technical analysis with candlestick, RSI, MACD, Bollinger Bands, volume charts
- **Watchlist**: Track favourite stocks with live price and signal updates
- **Portfolio**: Persistent SQLite-backed portfolio with buy/sell tracking, P&L, allocation charts
- **Paper Trading**: Simulate orders (market, limit, stop-loss, take-profit) with full trade history
- **Backtesting**: Select strategies, date ranges, parameters — view equity curves, drawdown, metrics
- **Market Regime**: Detected regime (Bull/Bear/Panic/Recovery) with confidence and reasons
- **Notifications**: Unified notification center for price alerts, scanner results, system events

### Data Service
Centralised data layer with automatic provider fallback, tiered caching, and live WebSocket feed.

**Single sources of truth (Sprint 10):** all CSV parsing flows through `src/loaders/csv_loader.py::load_csv` (with `resolve_stock_csv_path` for symbol→file resolution), confidence is computed by `src/decision/confidence.py`, API URLs live in `src/config.py`, and the project version is read from the root `VERSION` file via `src/version.py`.

- **HybridProvider**: Tries multiple NEPSE API sources → local CSV → cache → graceful empty
- **TieredCache**: In-memory (fast) + disk (persistent) with TTL expiry
- **Health Monitoring**: Auto-disables failing providers, re-enables after recovery period
- **Rate Limiter**: Token-bucket per provider with exponential backoff and jitter
- **Background Refresh**: Threaded cache refresh — no blocking on page renders
- **Request Metrics**: Track cache hit rate, API latency, provider failures (via Performance Debug page)

### Strategy & Analysis
- **Technical Indicators**: Moving Averages (20/50/180), RSI, MACD, ATR, Bollinger Bands, Volume Profile
- **Market Structure**: Support/resistance, trend detection, candlestick patterns
- **Signal Engine**: Score (0–100), confidence, trade plan with entry/stop-loss/targets
- **Market Scanner**: Rank, filter, sort by signal, score, regime across all stocks
- **Market Regime Detector**: Bull, Bear, Panic, Recovery, Accumulation, Distribution, Sideways

### Backtesting
- Replay historical candles with configurable commission and slippage
- Track exit reasons: `TARGET`, `STOP_LOSS`, `SELL_SIGNAL`, `END_OF_DATA`
- Compute Sharpe, Sortino, Calmar, Win Rate, Profit Factor, Expectancy, Drawdown

### Portfolio & Trading
- **Portfolio Database**: SQLite-backed holdings, transactions, realised/unrealised P&L
- **Paper Trading Engine**: Market/limit/stop-loss/take-profit orders with partial fills
- **Trade Journal**: Automatic logging, notes, lessons learned, emotion tracking
- **Market Replay**: Play/pause/step through historical sessions via the same UI

### API & Integrations
- **FastAPI REST** — Typed endpoints for analysis, backtest, portfolio, scanner, watchlist
- **Telegram Bot** — Optional client for monitoring and alerts
- **Export Center** — CSV, Excel, JSON, HTML, PDF reports

---

## Repository Structure

```text
.
├── .github/workflows/        # CI + Release workflows
├── assets/                   # Custom CSS and static assets
├── data/                     # Market CSV price data (volume-mounted in Docker)
├── docs/                     # Technical documentation
│   ├── ARCHITECTURE.md       # Architecture overview
│   ├── BACKTESTING.md        # Backtesting engine documentation
│   ├── DATA_SERVICE.md       # Data Service architecture
│   ├── DEPLOYMENT.md         # Deployment guide
│   ├── CHANGELOG.md          # Release history
│   ├── CONTRIBUTING.md       # Contribution guidelines
│   └── ROADMAP.md            # Development roadmap
├── launcher/                  # Native service launchers (bat / sh)
├── scripts/                   # Backup and maintenance scripts
├── src/
│   ├── alerts/                # Alert rules and notification centre
│   ├── api/                   # FastAPI REST endpoints
│   ├── backtest/              # Backtest engine, trade simulator, metrics
│   ├── bot/                   # Optional Telegram client
│   ├── data/                  # Centralised DataService, providers, cache, WebSocket
│   │   ├── service.py        # DataService singleton (single entry point)
│   │   ├── providers.py      # APIProvider, CSVProvider, HybridProvider
│   │   ├── cache.py          # MemoryCache, DiskCache, TieredCache
│   │   ├── models.py         # Dataclass models (MarketSummary, StockQuote, …)
│   │   ├── websocket.py      # LiveMarketStream with subscriber pattern
│   │   ├── rate_limiter.py   # Token-bucket rate limiter
│   │   ├── health.py         # Provider health monitoring
│   │   ├── metrics.py        # Request metrics collector
│   │   └── export.py         # Export Center (CSV/Excel/JSON/HTML/PDF)
│   ├── decision/              # Signal and decision logic
│   ├── engine/                # Core analysis pipeline
│   ├── indicators/            # Technical indicators
│   ├── logging/               # Centralised logging (env-configurable level)
│   ├── market_structure/      # Support, resistance, trend
│   ├── optimization/          # Parameter & walk-forward optimisation
│   ├── paper_trading/         # Paper trading engine (orders, positions, P&L)
│   ├── portfolio/             # Portfolio database and analytics
│   ├── regime/                # Market regime detector
│   ├── replay/                # Market replay engine
│   ├── scanner/               # Market scanning and ranking
│   ├── strategies/            # Trading strategies (momentum, breakout, adaptive)
│   ├── trading/               # Trade journal
│   ├── ui/                    # Streamlit frontend
│   │   ├── pages/             # 15+ page modules
│   │   ├── components/        # Reusable chart/KPI components
│   │   ├── helpers.py         # Formatting utilities
│   │   ├── theme.py           # Theme configuration
│   │   ├── state.py           # Session state management
│   │   ├── notifications.py   # Notification manager
│   │   └── shortcuts.py       # Keyboard shortcuts
│   └── watchlist/             # Watchlist management
├── tests/                     # 825+ automated tests
├── app.py                     # Streamlit entry point
├── Dockerfile                 # Multi-stage Docker build
├── docker-compose.yml         # Production Docker Compose
├── .env.example               # Environment variable template
└── README.md
```

---

## FastAPI Endpoints

| Category | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| **Health** | `GET` | `/` | Engine operational status |
| **Analysis** | `GET` | `/analyze/{symbol}` | Single-stock technical analysis |
| **Backtest** | `GET` | `/backtest/{symbol}` | Backtest execution |
| **Portfolio** | `GET` | `/portfolio/` | Portfolio analysis and PnL |
| **Scanner** | `GET` | `/market/` | Market-wide summary |
| **Scanner** | `GET` | `/market/top10` | Top 10 ranked candidates |
| **Scanner** | `GET` | `/market/buylist` | Active `BUY` signals |
| **Scanner** | `GET` | `/market/selllist` | Active `SELL` signals |
| **Scanner** | `GET` | `/market/strongbuy` | High-confidence `STRONG BUY` candidates |
| **Watchlist** | `GET` | `/watchlist` | Current watchlist symbols |
| **Watchlist** | `POST` | `/watchlist/add/{symbol}` | Add symbol to watchlist |
| **Watchlist** | `DELETE` | `/watchlist/remove/{symbol}` | Remove symbol from watchlist |
| **Watchlist** | `GET` | `/watchlist/scan` | Scan watchlist for signals |

---

## Installation

### Requirements

- Python 3.12+
- pip

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and customise:

```bash
cp .env.example .env
```

---

## Running Locally

### Streamlit Frontend

```bash
python -m streamlit run app.py
# Open http://localhost:8501
```

### FastAPI Backend

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

### Launcher Scripts

**Linux / macOS:**
```bash
./launcher/run_app.sh          # Streamlit frontend
./launcher/run_app.sh --api    # FastAPI backend
./launcher/run_app.sh --all    # Both via tmux
```

**Windows:**
```cmd
launcher\run_app.bat
launcher\run_app.bat --api
```

### Telegram Bot (Optional)

```bash
# Set TELEGRAM_TOKEN in .env, then:
python -m src.bot.telegram_bot
```

### Run Tests

```bash
python -m pytest tests/ -q --tb=short
```

---

## Docker Deployment

```bash
docker compose up -d web          # Streamlit frontend
docker compose up -d              # All services
docker compose --profile bot up   # Include Telegram bot
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for production deployment (systemd, backup/restore, security, monitoring).

---

## API Quick Start

```python
from src.data import DataService

svc = DataService()
summary = svc.get_market_summary()
quote = svc.get_stock("NABIL")
history = svc.get_history("NABIL", days=365)

print(f"NEPSE Index: {summary.index:.2f}")
print(f"NABIL Quote: {quote.ltp}")
```

---

## Troubleshooting

### In-app terminal fails to run commands (Windows)

If the desktop app's embedded terminal cannot run any command and prints

```
Skipping command-line "C:\Program Files\Git\bin\..\usr\bin\bash.exe" ... not found
```

your Git for Windows installation is partial or broken: the launcher shim
(`C:\Program Files\Git\bin\bash.exe`) exists but the real bash
(`C:\Program Files\Git\usr\bin\bash.exe`) is missing. Run the bundled
repair launcher:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\fix_inapp_terminal.bat
```

It diagnoses the install, locates the real `bash.exe`, and creates a
junction at the standard Git path (elevated when needed). Fully restart the
desktop app afterwards so the terminal picks up the fixed path.

---

## Roadmap

For details on planned features, upcoming enhancements, and long-term milestones, refer to the [ROADMAP.md](docs/ROADMAP.md).

---

## Contributing

Contributions are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a feature or fix branch.
3. Add or update tests where appropriate.
4. Open a pull request with a clear description of the change.

Please keep changes focused, documented, and aligned with the project’s research-oriented scope.

---

## License

This repository does not currently include an open-source license file. If you intend to reuse or redistribute the project publicly, please add an explicit open-source license before distribution.
