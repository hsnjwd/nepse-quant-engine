# NEPSE Quant Engine — Architecture v2

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Streamlit Frontend                              │
│  ┌─────────┬──────────┬──────────┬──────────┬──────────┬──────────┐    │
│  │Dashboard│ Scanner  │Analyze   │Portfolio │ Backtest │  Alerts/ │    │
│  │         │          │Stock     │+Paper    │/Optimizer│  Notif.  │    │
│  ├─────────┼──────────┼──────────┼──────────┼──────────┼──────────┤    │
│  │Advanced │Market    │Portfolio │Performance│  System  │  Cache & │    │
│  │ Charts  │ Regime   │Analytics │Dashboard │  Status  │  Perform │    │
│  └─────────┴──────────┴──────────┴──────────┴──────────┴──────────┘    │
│                                  │                                      │
│                    ┌─────────────┴─────────────┐                        │
│                    │       DataService          │                       │
│                    │        (Singleton)         │                       │
│                    └──────┬──────────┬──────────┘                       │
│                           │          │                                   │
└───────────────────────────┼──────────┼─────────────────────────────────┘
                            │          │
         ┌──────────────────┼──────────┼─────────────────────────┐
         │                  │          │                          │
    ┌────┴─────┐      ┌────┴─────┐    │    ┌────────────────┐    │
    │ Hybrid   │      │ Tiered   │    │    │ Infrastructure │    │
    │ Provider │◄─────┤ Cache    │    │    │                │    │
    ├──────────┤      ├──────────┤    │    ├────────────────┤    │
    │ APIProv  │      │Memory    │    │    │ Notification   │    │
    │ CSVProv  │      │Disk      │    │    │ Manager        │    │
    └──────────┘      └──────────┘    │    ├────────────────┤    │
                                      │    │ Trade Journal  │    │
         ┌────────────────────────────┘    ├────────────────┤    │
         │                                 │ Theme Manager │    │
    ┌────┴──────────────────────┐          ├────────────────┤    │
    │     Live Data Sources     │          │Export Center   │    │
    ├───────────────────────────┤          ├────────────────┤    │
    │ WebSocket (live stream)   │          │Keyboard        │    │
    │ NEPSE API (polling)       │          │ Shortcuts      │    │
    │ Local CSV (fallback)      │          │                │    │
    │ Market Replay engine      │          │User Settings   │    │
    └───────────────────────────┘          └────────────────┘    │
```

## Module Responsibilities

### Data Layer (`src/data/`)
- **DataService**: Singleton entry point for all market data. Integrates providers, cache, WebSocket, rate limiter, health monitor, and metrics.
- **HybridProvider**: Routes requests through a priority chain of providers (API → CSV → cached → safe empty).
- **TieredCache**: Two-level cache (memory + disk) with TTL support.
- **WebSocket**: LiveMarketStream with auto-reconnect, heartbeat, subscriber pattern.
- **RateLimiter**: Token-bucket rate limiter with per-provider limits, backoff, jitter.
- **HealthMonitor**: Tracks provider health, auto-disable on failures, auto-re-enable after recovery.
- **MetricsCollector**: Thread-safe collection of request metrics, cache stats, latency percentiles.

### Portfolio (`src/portfolio/`)
- **Database**: SQLite-backed portfolio storage with WAL mode, thread-safe CRUD.
- **Models**: PortfolioHolding, PortfolioTransaction, PortfolioSummary, PortfolioAnalytics.

### Paper Trading (`src/paper_trading/`)
- **Engine**: Order execution (market/limit/stop-loss/take-profit), position management, P&L tracking.
- **Models**: Order, OpenPosition, PaperTrade, PaperTradingSummary.

### Alert Center (`src/alerts/`)
- **AlertCenter**: Rule-based alert triggering (price, volume, RSI, MACD, breakout, regime, portfolio).
- **NotificationManager**: Unified notification system with categories, priority, persistence.

### Trading Journal (`src/trading/`)
- **TradeJournal**: Automatic trade logging, R-multiple tracking, win/loss analysis, CSV/JSON export.

### Market Replay (`src/replay/`)
- **MarketReplayEngine**: Play/pause/step/fast-forward/rewind historical data with cache injection.

### UI (`src/ui/`)
- **20+ pages** organized by section (Main, Market, Portfolio, Strategies, Monitoring, System).
- **Components**: Reusable KPI cards, badges, charts (Plotly), progress bars.
- **ThemeManager**: Dark/Light/TradingView/Bloomberg themes with CSS variables.
- **Notifications**: Unified notification center with badge count, search, categories.
- **Shortcuts**: Global keyboard shortcuts via JavaScript injection.

## Request Flow

```
User Action → Streamlit Rerun → Page render() → DataService.get_*()
                                                      │
                                              ┌───────┴──────┐
                                              │  Check Cache  │
                                              └───────┬──────┘
                                         Hit? ──┐     │ Miss?
                                            │   │     │    │
                                            ▼   │     ▼    │
                                        Return◄─┘ Acquire  │
                                        cached       │     │
                                        data     ┌────▼────┘
                                                 │ Rate     │
                                                 │ Limiter  │
                                                 └────┬────┘
                                           Limited?    │ Pass?
                                              │         │
                                              ▼         ▼
                                          Return  ┌──────────┐
                                          empty   │Provider   │
                                                  │Chain      │
                                                  └────┬─────┘
                                                       │
                                            ┌──────────┼──────────┐
                                            │          │          │
                                         APIProv   CSVProv   Cached
                                        Success     Success   (stale)
                                            │          │          │
                                            └──────────┼──────────┘
                                                       │
                                                   Cache.set()
                                                       │
                                                   Return data
```

## Cache Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                       TieredCache                               │
│  ┌─────────────────────┐    ┌───────────────────────────────┐   │
│  │   MemoryCache       │    │       DiskCache               │   │
│  │   (in-process dict) │◄──►│   (JSON files on disk)       │   │
│  │   TTL: 60s default  │    │   TTL: 600s default          │   │
│  │   Thread-safe       │    │   Survives restarts          │   │
│  └─────────────────────┘    └───────────────────────────────┘   │
│         │                          │                            │
│         │     Promotion: disk → memory on access               │
│         └──────────────────────────┘                            │
│                                                                 │
│  Key format: "market_summary", "history:NABIL:365",             │
│              "live_quotes", "nepse_index:500",                  │
│              "top_gainers:10", "market_scan"                    │
└─────────────────────────────────────────────────────────────────┘
```

## WebSocket Flow

```
LiveMarketStream (background thread)
       │
       ├── connect(url)
       ├── heartbeat (30s interval)
       ├── _listen() loop
       │       │
       │       ├── On "quote" → StockQuote → callbacks
       │       ├── On "summary" → MarketSummary → callbacks
       │       └── On other → MessageCallback
       │
       ├── On disconnect → _reconnect_delay(attempt)
       │       ├── exponential backoff: 2^(n-1) seconds
       │       ├── jitter: ±10%
       │       └── max: 60s
       │
       └── callbacks (thread-safe)
               ├── _on_quote → update live_quotes cache
               └── _on_summary → update market_summary cache
```

## Keyboard Shortcuts Flow

```
User presses Ctrl+P
       │
       ▼
JavaScript keydown listener
       │
       ├── Ctrl+K → focus symbol/global search input
       ├── Ctrl+R → let browser handle (native refresh)
       ├── Ctrl+P → find and click Portfolio button
       ├── Ctrl+S → find and click Scanner button
       ├── Ctrl+B → find and click Backtest button
       ├── Ctrl+D → find and click Dashboard button
       ├── Ctrl+H → find and click System Status button
       └── ? → toggle help overlay visibility
```

## Sprint 8 — AI & Quantitative Trading Layer

```
                 ┌────────────────────────────────────────────────┐
                 │           Sprint 8 AI Platform                 │
                 │                                                │
  ┌────────────┐ │  ┌────────────┐  ┌────────────┐  ┌──────────┐ │
  │ Streamlit  │ │  │  ML Engine │  │   AI       │  │ Strategy │ │
  │ AI Pages   │◄┼──┤  (src/ml)  │  │  Advisor   │  │ Builder  │ │
  │ (11 new)   │ │  │            │  │  (src/ai)  │  │ (strategy│ │
  └────────────┘ │  └────────────┘  └────────────┘  │  _builder)│ │
                 │  ┌────────────┐  ┌────────────┐  └──────────┘ │
  ┌────────────┐ │  │ Optimizer  │  │  Risk Lab  │  ┌──────────┐ │
  │ FastAPI    │◄┼──┤ MPT/RP/GA  │  │  (src/risk)│  │ Strategy │ │
  │ (/stocks,  │ │  │ (src/opt)  │  │            │  │ Market-  │ │
  │ /signals,  │ │  └────────────┘  └────────────┘  │  place   │ │
  │ /models,   │ │  ┌────────────┐  ┌────────────┐  └──────────┘ │
  │ /risk,     │ │  │  Plugins   │  │  Brokers   │  ┌──────────┐ │
  │ /replay…)  │ │  │ (src/plug) │  │  (src/bro) │  │   Sync   │ │
  └────────────┘ │  └────────────┘  └────────────┘  │ (src/sync)│ │
                 │                                  └──────────┘ │
                 └───────────────────┬────────────────────────────┘
                                     │
                               DataService
                               (all data flows through here)
```

### New modules

| Module | Purpose |
| --- | --- |
| `src/ml/` | Feature engineering, datasets, model wrappers (sklearn/xgboost/lightgbm with numpy fallbacks), training, evaluation, CV, versioning, persistence, prediction |
| `src/ai/` | Rule-based signal explanations, market summaries, trade explanations, portfolio reviews, daily reports (LLM-replaceable later) |
| `src/strategy_builder/` | Declarative rule trees (AND/OR/NOT, comparisons), JSON serialization, validation, instant backtest |
| `src/strategies/` (extended) | Auto-discovery marketplace: momentum, breakout, mean reversion, trend following, gap, opening range, volume spike, AI trend |
| `src/optimization/` | MPT / efficient frontier / min variance / max Sharpe / risk parity / Kelly / genetic algorithm |
| `src/risk/lab.py` | Monte Carlo, VaR, CVaR, stress tests, ruin probability, drawdown probability, recovery analysis |
| `src/plugins/` + `plugins/` | Auto-discovered plugins: indicators, reports, strategies, alerts, exporters, notifications, brokers |
| `src/brokers/` | Broker ABC + Paper/Mock/FutureNEPSE + IBKR/Binance/Alpaca adapter stubs |
| `src/api/` (extended) | `/stocks`, `/signals`, `/strategies`, `/models`, `/optimizer`, `/risk`, `/replay` routers with pagination |
| `src/sync/` | Cloud-sync provider abstraction (watchlists, alerts, settings, portfolio, strategies, reports) |
| `src/logging/enterprise.py` | Structured JSON logging with rotation/retention |

### Request flow (Sprint 8 example)

```
POST /models/train {symbol: NABIL}
        │
        ▼
DataService().get_history("NABIL", days=365)   ← cache-first
        │
        ▼
TrainingPipeline().run(df, model_name=..., task=...)
        │
        ├── FeatureEngineer.generate(df)
        ├── DatasetBuilder.build_classification(df)
        ├── get_model(name).fit(train)          ← numpy fallback if needed
        ├── evaluate_classification(test)
        └── ModelManager.save(model)            ← versioned pickle + metadata
        │
        ▼
TrainingResult.to_dict() → {success, data}
```

## Configuration

Settings are stored in `~/.nepse/user_settings.json` with 30+ configurable options covering theme, trading defaults, cache, display, notifications, charts, data sources, and WebSocket.

## Deployment

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
python -m streamlit run app.py

# With custom config
WEBSOCKET_ENABLED=true python -m streamlit run app.py
```
