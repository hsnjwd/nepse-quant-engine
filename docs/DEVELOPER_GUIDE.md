# NEPSE Quant Engine — Developer Guide

## Project Structure

```
src/
├── data/              # Data layer (DataService, providers, cache, websocket, metrics)
├── portfolio/         # Portfolio database, models, analytics
├── paper_trading/     # Paper trading engine, order models
├── alerts/            # Alert center, rules engine, notification center
├── trading/           # Trade journal, statistics
├── replay/            # Market replay engine
├── ui/                # Streamlit frontend (20+ pages, components, theme, state)
│   ├── pages/         # Page render functions
│   ├── components/    # Reusable UI components (charts, badges, cards)
│   └── theme.py       # Theme singleton
├── config/            # Configuration (env vars, user settings)
├── engine/            # Analysis engine
├── regime/            # Market regime detector
├── scanner/           # Market scanner
├── strategies/        # Trading strategies
├── backtest/          # Backtesting engine
├── indicators/        # Technical indicators
├── risk/              # Risk management
├── signals/           # Signal scoring
└── api/               # FastAPI backend
```

## Key Design Patterns

### Singleton Pattern

```python
# src/data/service.py
class DataService:
    _instance: DataService | None = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
```

Used by: `DataService`, `NotificationManager`, `UserSettings`, `ThemeManager`, `TradeJournal`.

### Provider Chain Pattern

```python
class HybridProvider(BaseProvider):
    def _try_all(self, method, *args, **kwargs):
        errors = []
        for provider in self._providers:
            try:
                fn = getattr(provider, method)
                return fn(*args, **kwargs)
            except Exception as exc:
                errors.append(f"[{provider.name}] {exc}")
                continue
        raise DataUnavailable(...)
```

### Tiered Cache

```python
class TieredCache:
    def get(self, key):
        # 1. Try memory cache
        val = self._memory.get(key)
        if val is not None:
            return val
        # 2. Try disk cache (promotes to memory)
        val = self._disk.get(key)
        if val is not None:
            self._memory.set(key, val)
        return val
```

## Adding a New Page

1. Create `src/ui/pages/your_page.py` with a `render()` function
2. Add page routing in `app.py`
3. Add sidebar navigation button
4. Use `DataService` for all market data
5. Use `src.ui.helpers` for formatting (safe_get, safe_float, fmt_rupees, etc.)
6. Use `src.ui.components` for reusable UI (kpi_card, pie_chart, line_chart, badges)
7. Handle errors gracefully — never let exceptions reach the user
8. Add tests in `tests/`

Example page skeleton:
```python
"""Your Page — description."""

from __future__ import annotations
import streamlit as st
from src.ui.helpers import section_header, divider
from src.ui.components import kpi_card
from src.data import DataService as _DataService

def _svc() -> _DataService:
    return _DataService()

def render() -> None:
    section_header("Your Page", "Description")
    svc = _svc()
    try:
        data = svc.get_market_summary()
        st.metric("NEPSE", data.index)
    except Exception as e:
        st.info(f"Data unavailable: {e}")
```

## Adding a New Provider

1. Create a class inheriting from `BaseProvider`
2. Implement all abstract methods (`_do_market_summary`, `_do_live_quotes`, `_do_history`, `_do_nepse_index_history`, `_do_top_gainers`, etc.)
3. Set `name` class variable
4. Register in `HybridProvider` priority chain
5. Add tests with mock responses

```python
class MyProvider(BaseProvider):
    name = "my_provider"

    def _do_market_summary(self) -> MarketSummary:
        # Fetch from your API
        return MarketSummary(...)
```

## Testing

### Running Tests
```bash
python -m pytest tests/ -v
python -m pytest tests/test_data_service.py -v
python -m pytest tests/test_sprint5_modules.py -v
python -m pytest tests/test_sprint6_modules.py -v
python -m pytest tests/test_sprint65_modules.py -v
```

### Writing Tests
- Use `pytest` framework
- Mock external APIs with `unittest.mock.MagicMock`
- Use `tmp_path` fixture for file-based tests
- Test edge cases: empty data, None values, API failures, CSV fallback
- Test thread safety with `threading.Thread`
- Keep tests focused on one behavior per test function

### Test Structure
```
tests/
├── conftest.py              # Shared fixtures
├── test_data_service.py     # DataService, cache, providers, metrics (~200 tests)
├── test_sprint5_modules.py  # Portfolio, paper trading, alerts, export (~100 tests)
├── test_sprint6_modules.py  # Trade journal, replay, theme, settings (~100 tests)
├── test_sprint65_modules.py # Notifications, shortcuts, scanner filters (~120 tests)
└── test_*.py               # Engine, indicators, strategy tests (~300 tests)
```

## Exception Hierarchy

```
DataServiceError
├── ProviderError     # Provider-level failures
├── CacheError        # Cache-level failures
└── DataUnavailable   # No data available from any source
```

## Configuration

### Environment Variables
See `.env.example` for all supported variables. Key ones:
- `NEPSE_SCRAPER_URL`: NEPSE API endpoint
- `WEBSOCKET_ENABLED`: Enable WebSocket live feed
- `CACHE_MEMORY_TTL`: Memory cache TTL in seconds
- `CACHE_DISK_TTL`: Disk cache TTL in seconds
- `ENABLE_PERFORMANCE_MONITORING`: Collect request metrics

### User Settings
Stored in `~/.nepse/user_settings.json`. All settings are optional with sensible defaults.

## Contributing

1. Follow PEP8 with line length of 120
2. Use type hints everywhere (Python 3.12+)
3. Use dataclasses for data models
4. Write docstrings for all public functions
5. Add tests for all new functionality
6. Maintain backward compatibility
7. Run full test suite before submitting
8. Do NOT modify strategy, indicator, or scoring logic unless explicitly required

## Performance Guidelines

- Use `DataService` singleton to avoid duplicate API calls
- Cache expensive computations in `st.session_state`
- Use thread-safe queues for WebSocket callbacks (not direct `st.session_state` writes)
- Lazy-import heavy modules inside function bodies
- Measure page render time for performance-critical pages
