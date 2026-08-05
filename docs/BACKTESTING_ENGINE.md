# Institutional Backtesting Engine

Sprint 9 (v3.0) introduces a professional, event-driven backtesting
platform inspired by QuantConnect, Zipline, Backtrader, and vectorbt.
It lives in a new package — `src/backtesting/` — and is fully additive:
the legacy `src/backtest/` engine, DataService, strategy framework, and
all existing pages continue to work unchanged.

## Architecture

```
                    ┌─────────────────────────────┐
                    │        BacktestEngine       │
                    │  (event-driven, bar-by-bar) │
                    └──────┬──────────┬───────────┘
                           │          │
              ┌────────────▼───┐  ┌───▼────────────────┐
              │  BacktestTimeline  │  │  EventBus          │
              │  (multi-symbol)    │  │  (pub/sub)         │
              └────────────┬───┘  └───┬────────────────┘
                           │          │
    ┌──────────────────────▼──────────▼──────────────────────┐
    │ OrderManager   →  Order lifecycle + advanced order types │
    │ FillEngine     →  slippage + commission + tax + stamp    │
    │ PortfolioSimulator → cash, positions, leverage, short    │
    │ CorporateActionEngine → splits, dividends, rights, etc.  │
    └──────────────────────────────────────────────────────────┘
                           │
    ┌──────────────────────▼──────────────────────┐
    │ AdvancedPerformanceAnalyzer → metrics       │
    │ InstitutionalReport → HTML / PDF / Excel / JSON │
    └─────────────────────────────────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `models.py` | Core dataclasses: `Bar`, `Order`, `Fill`, `Position`, `Trade`, `BacktestConfig`, enums |
| `events.py` | `EventBus` publish/subscribe and `BacktestEvent` |
| `timeline.py` | Synchronised multi-symbol bar timeline |
| `orders.py` | `OrderManager`, lifecycle state machine, bracket/OCO/IOC/FOK/GTC/GTD |
| `fills.py` | `FillEngine` fill decisions and execution pricing |
| `slippage.py` | Fixed / percentage / volume / volatility / spread / random models |
| `commission.py` | Flat / percentage / tiered / broker models + `TaxPolicy` |
| `portfolio.py` | Cash, long/short positions, leverage, sector/industry allocation |
| `statistics.py` | Rolling Sharpe/Sortino, ulcer index, omega, SQN, alpha/beta, etc. |
| `engine.py` | `BacktestEngine` + `BacktestResult` |
| `corporate_actions.py` | Splits, reverse splits, bonus, dividends, rights, delisting, renames |
| `scenarios.py` | Bull/bear/sideways/volatility/flash-crash/liquidity/random stress tests |
| `walk_forward.py` | Rolling/expanding windows, parameter persistence |
| `multi_timeframe.py` | Daily/weekly/monthly synchronisation |
| `reports.py` | Institutional HTML/PDF/Excel/JSON reports + AI commentary |
| `tournament.py` | `StrategyTournament` rankings |
| `replay_bridge.py` | Merges `MarketReplayEngine` with `BacktestEngine` |

## Quick start

```python
from src.backtesting import BacktestEngine, BacktestConfig
from src.backtesting.orders import OrderManager

def strategy(bar_index, bars, ctx):
    manager: OrderManager = ctx["order_manager"]
    if bar_index == 10:
        return [manager.market("NABIL", "BUY", 100)]
    return []

engine = BacktestEngine(
    config=BacktestConfig(initial_cash=1_000_000, slippage_model="volume",
                          commission_model="broker"),
    strategy=strategy,
)
result = engine.run({"NABIL": df})
print(result.metrics.sharpe, len(result.trades))
```

## Order lifecycle

```
CREATED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED
                          ↘ CANCELLED
                          ↘ REJECTED
                          ↘ EXPIRED
```

`OrderManager` enforces valid transitions and raises `ValueError` on
illegal ones. IOC orders have their remainder cancelled after the bar;
OCO siblings are cancelled when a partner fills; GTD orders expire at
their `expires_at`.

## Multi-asset

`PortfolioSimulator` supports multiple simultaneous symbols, long and
short positions, leverage caps, sector/industry allocation,
concentration (Herfindahl), and correlation exposure. Fills are
reconciled into closed trades by the engine, including reversals.

## See also

- `docs/EXECUTION_ENGINE.md` — order types, slippage, commission.
- `docs/PERFORMANCE_ANALYTICS.md` — metrics reference.
- `docs/STRATEGY_TOURNAMENT.md` — tournament UI and ranking.
