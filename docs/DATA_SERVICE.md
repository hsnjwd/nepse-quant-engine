# DataService — Centralized Data Layer

## Architecture

```
                    ┌───────────────────────────────────┐
                    │           DataService             │
                    │        (singleton)                │
                    └──────┬────────────┬───────────────┘
                           │            │
              ┌────────────┼────────────┼──────────────┐
              ▼            ▼            ▼              ▼
       ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────┐
       │ Tiered   │ │  Hybrid   │ │ Metrics  │ │  WebSocket│
       │ Cache    │ │ Provider  │ │Collector │ │LiveStream│
       └────┬─────┘ └─────┬─────┘ └──────────┘ └──────────┘
            │             │
            ▼             ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ Memory   │   │   API    │   │   CSV    │   │ Health   │
   │ Cache    │   │ Provider │←──│ Provider │   │ Monitor  │
   └────┬─────┘   └────┬─────┘   └──────────┘   └────┬─────┘
        │              │                             │
        ▼              ▼                             ▼
   ┌──────────┐   ┌──────────┐               ┌──────────┐
   │  Disk    │   │  Rate    │               │  Auto-   │
   │  Cache   │   │ Limiter  │               │ Disable  │
   └──────────┘   └──────────┘               └──────────┘
```

## Design Principles

1. **Single source of truth** — Every data request flows through `DataService`
2. **Singleton** — One shared instance across all pages; cache state is universal
3. **Pluggable providers** — Swap data sources without changing consumer code
4. **Automatic fallback** — If API fails, CSV is tried. If all fail, empty defaults
5. **Tiered caching** — Memory (fast) → Disk (persistent) → Provider (source)
6. **Never crash** — All errors are caught, logged, and return graceful defaults

## Files

| File | Purpose |
|------|---------|
| `src/data/__init__.py` | Public API exports |
| `src/data/service.py` | `DataService` — the main entry point |
| `src/data/providers.py` | `BaseProvider`, `CSVProvider`, `APIProvider`, `HybridProvider` |
| `src/data/cache.py` | `MemoryCache`, `DiskCache`, `TieredCache` |
| `src/data/models.py` | All dataclasses (`MarketSummary`, `StockQuote`, etc.) |
| `src/data/exceptions.py` | Exception hierarchy |
| `src/data/websocket.py` | `LiveMarketStream` — WebSocket live feed |
| `src/data/rate_limiter.py` | `RateLimiter` — token-bucket rate limiting |
| `src/data/health.py` | `ProviderHealthMonitor` — auto-disable/re-enable |
| `src/data/metrics.py` | `MetricsCollector` — request metrics |

## Quick Start

```python
from src.data import DataService

svc = DataService()

# Market overview
summary = svc.get_market_summary()
print(summary.index, summary.change_pct, summary.status)

# Live stock quote
nabil = svc.get_stock("NABIL")
if nabil:
    print(f"{nabil.symbol}: ₹{nabil.ltp} ({nabil.change_pct:+.2f}%)")

# Price history
history = svc.get_history("NABIL", days=365)
if not history.is_empty:
    print(f"Latest close: {history.latest_close}")

# Market scan
scan = svc.scan_market()
print(f"Buy: {scan.buy_count}, Sell: {scan.sell_count}")

# Top movers
for g in svc.get_top_gainers(5):
    print(f"{g.symbol}: {g.change_pct:+.2f}%")

# Get performance metrics
metrics = svc.get_metrics()
print(f"Cache hit rate: {metrics.cache_hit_rate:.1f}%")
print(f"P95 latency: {metrics.p95_latency_ms:.1f} ms")
```

## WebSocket Live Feed

The `LiveMarketStream` provides real-time market data via WebSocket.

### Configuration

```python
from src.data import DataService, WebSocketConfig

svc = DataService()
svc.start_live_feed(url="wss://your-websocket-server/ws")
```

The WebSocket stream:
- **Auto-reconnects** with exponential backoff (1s → 2s → 4s ... → 60s max)
- **Sends heartbeats** every 30 seconds
- **Dispatches updates** to subscriber callbacks
- **Updates the cache** directly — pages read fresh data without polling
- **Falls back** to API polling if WebSocket is unavailable

### Subscriber Pattern

```python
def on_quote(quote):
    print(f"{quote.symbol}: ₹{quote.ltp}")

def on_summary(summary):
    print(f"NEPSE: {summary.index}")

svc.subscribe_market(on_quote)     # quote callbacks
svc.unsubscribe_market(on_quote)   # unsubscribe
```

### Status

```python
svc.is_live_feed_connected()   # bool
stats = svc.get_websocket_stats()  # WebSocketStats dataclass
print(stats.reconnect_count)       # int
print(stats.messages_received)     # int
```

## Rate Limiter

Token-bucket rate limiter with per-provider limits, exponential backoff,
and Retry-After support.

```python
from src.data.rate_limiter import RateLimiter

limiter = RateLimiter()
limiter.configure("nepse_scraper", max_tokens=10, refill_rate=1.0)

if limiter.acquire("nepse_scraper"):
    # Make API call
    limiter.record_success("nepse_scraper")
else:
    # Rate limited — wait
```

Key features:
- **Per-provider token buckets** configured independently
- **Exponential backoff** with jitter: `backoff_base * 2^(failures - 1)`
- **429/503 handling** triggers provider cooldown with Retry-After
- **Configurable defaults**: `configure_defaults(max_tokens, refill_rate, backoff_base, backoff_max, jitter)`

## Provider Health Monitor

Tracks provider health and auto-disables failing providers.

```python
monitor = svc.health_monitor

# Auto-disable after 5 consecutive failures
monitor.record_success("api", latency_ms=150)
monitor.record_failure("api")

# Check health
if monitor.is_healthy("api"):
    ...

# Manual control
monitor.enable("api")
monitor.disable("api")
```

Configuration:
- `failure_threshold=5` — consecutive failures before disable
- `recovery_period=300s` — wait before trial re-enable
- `success_recovery_count=3` — successes needed to clear disabled state

### Provider Selection Algorithm

The `HybridProvider` uses health data to select the best provider:

1. Call `_get_healthy_providers()` to filter disabled providers
2. Sort remaining by success rate (highest first)
3. Try each provider in order
4. If all disabled, fall back to original list
5. Track `last_provider` for debugging

```python
# Health-aware HybridProvider (wired automatically by DataService)
provider = HybridProvider(providers, health_monitor=monitor)
```

## Metrics Collection

Collects per-operation and per-provider metrics.

```python
metrics = svc.get_metrics()

# Global stats
metrics.total_requests     # int
metrics.cache_hit_rate    # float (percentage)
metrics.failure_rate      # float (percentage)
metrics.average_latency_ms  # float
metrics.p95_latency_ms      # float
metrics.p99_latency_ms      # float

# Per-operation
op = metrics.per_operation["get_market_summary"]
op.calls, op.cache_hits, op.average_latency_ms, op.p95_latency_ms, op.p99_latency_ms

# Per-provider
pm = metrics.per_provider["api"]
pm.calls, pm.successes, pm.failures, pm.average_latency_ms
```

Reset metrics at any time:
```python
svc.reset_metrics()
```

## Background Refresh

Upgraded refresh thread with full lifecycle control.

```python
svc.start_background_refresh(interval=60)  # start
svc.pause_background_refresh()             # pause
svc.resume_background_refresh()            # resume
svc.set_refresh_interval(120)              # dynamic interval
svc.stop_background_refresh()              # graceful shutdown

# Status
svc.is_background_refresh_running()      # bool
svc.is_background_refresh_paused()       # bool
```

Features:
- **Thread-safe** — no duplicate threads (idempotent start)
- **Pause/Resume** — suspends refresh without stopping the thread
- **Dynamic interval** — changes take effect on next refresh cycle
- **Graceful shutdown** — joins thread with 5-second timeout
- **Automatic recovery** — exceptions are logged, loop continues

## Performance Dashboard

Access the performance dashboard at **📦 Cache Debug** in the sidebar.

Seven tabs:
| Tab | Content |
|-----|---------|
| 📦 Cache | Memory/disk entries, TTL, controls |
| ⚡ Metrics | Request counts, cache hit rate, latency (P50/P95/P99), failure gauge |
| 🔌 Providers | Provider chain, health status, latency comparison |
| 🌐 WebSocket | Connection status, reconnects, messages, start/stop controls |
| 🔄 Background | Thread status, pause/resume, interval configuration |
| ⚙️ Rate Limiter | Per-provider tokens, cooldowns, 429/503 counts |
| 🧠 Health | Provider health table, enable all/reset controls |

Uses Plotly for charts with graceful fallback if Plotly is not installed.

## Configuration

All settings in `src/config.py`, configurable via environment variables:

```python
# WebSocket
WEBSOCKET_ENABLED = False        # Enable WebSocket live feed
WEBSOCKET_URL = "wss://..."      # WebSocket server URL

# Rate Limiter
RATE_LIMIT = 10                  # Default max tokens per provider
API_TIMEOUT = 15                 # API request timeout (seconds)
RETRY_COUNT = 3                  # Max retries on failure
BACKOFF_BASE = 1.0               # Initial backoff delay (seconds)

# Cache & Refresh
CACHE_REFRESH_INTERVAL = 60      # Background refresh interval (seconds)
HEALTH_CHECK_INTERVAL = 60       # Health monitor recheck interval

# Performance
ENABLE_PERFORMANCE_MONITORING = False  # Enable metrics collection
```

Override at runtime:
```python
DataService.configure({"nepse_scraper": "https://my-api.com/v1"})
```

## Exception Hierarchy

```
DataServiceError
├── ProviderError     # A provider failed (API timeout, bad response)
├── CacheError        # Cache read/write failure
└── DataUnavailable   # No provider could supply the data
```

## Migration from live_data.py

The `src/data/live_data.py` module is **deprecated**. Replace imports:

```python
# ❌ Old
from src.data.live_data import get_market_summary, get_stock, get_price_history

# ✅ New
from src.data import DataService
svc = DataService()
svc.get_market_summary()
svc.get_stock("NABIL")
svc.get_history("NABIL")
```

## Extension Guide

### Adding a New Data Method

1. Add model to `src/data/models.py`
2. Add `_do_*` abstract method to `BaseProvider`
3. Implement in `CSVProvider` and `APIProvider`
4. Add `get_*` method to `DataService` with caching
5. Add `_try_all` routing to `HybridProvider` if needed
6. Export from `src/data/__init__.py`
7. Write tests

### Adding a New Cache Backend

1. Implement `get()`, `set()`, `delete()`, `clear()`, `has()`
2. Compose into `TieredCache` or use standalone

### Adding a New Provider

```python
from src.data import BaseProvider, MarketSummary, StockQuote

class MyCustomProvider(BaseProvider):
    name = "custom"

    def _do_market_summary(self) -> MarketSummary:
        return MarketSummary(index=2100.0, status="Open")

    def _do_live_quotes(self) -> list[StockQuote]:
        return [StockQuote(symbol="TEST", ltp=100.0)]

    def _do_history(self, symbol: str, days: int) -> pd.DataFrame:
        return pd.DataFrame(...)

from src.data import HybridProvider, DataService
svc = DataService(provider=HybridProvider([
    MyCustomProvider(),
    svc.provider,
]))
```
