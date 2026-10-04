# Strategy Tournament

`src/backtesting/tournament.py` compares multiple strategies over shared
data, computes comparable metrics, and ranks them. The Streamlit page
**Strategy Comparison** (`strategy_comparison_page`) exposes it in the UI.

## Concept

A "strategy" is any callable compatible with `BacktestEngine`:

```python
def strategy(bar_index, bars, ctx) -> list[Order]:
    manager: OrderManager = ctx["order_manager"]
    ...
    return [manager.market("NABIL", "BUY", 100)]
```

All strategies run against the **same** data with the **same**
configuration, so differences are attributable to the strategy, not the
environment.

## Running

```python
from src.backtesting import StrategyTournament, BacktestConfig

tournament = StrategyTournament(config=BacktestConfig(initial_cash=1_000_000))
entries = tournament.run(
    data={"NABIL": df},
    strategies={"Momentum": momentum_fn, "Breakout": breakout_fn},
)
ranked = tournament.rank(entries)          # default sort: ai_score
table = tournament.ranking_table()         # JSON-ready dicts
```

## Ranking keys

`rank(entries, key=...)` supports sorting by:

- `ai_score` (default) — composite 0–100 quality score
- `total_return`, `sharpe`, `sortino`, `sqn`, `win_rate`

## AI quality score

The composite `ai_score` weights:

- Total return — 30 points
- Sharpe ratio — 30 points (capped at 3.0)
- Max drawdown — 20 points (inverse, 30% = 0)
- Win rate — 20 points

## Presets

The UI ships six presets (Momentum, Breakout, Mean Reversion, Trend
Following, Gap, Opening Range, AI Trend) each with a configured entry
order type and parameters, so users can run a tournament with one click.

## Resilience

Strategies that raise are caught, logged, and reported with
`metadata["error"]` so a single broken strategy never crashes the page
or tournament.
