# Parameter Optimisation Module

## Overview

The Parameter Optimisation Engine automatically searches for the best parameter combinations for any registered trading strategy. It supports **grid search** (exhaustive over all combinations) and **random search** (sampling from ranges), with the architecture designed so that Bayesian optimisation can be added later.

This module works with all existing components:

- **Strategy Registry** — retrieves strategy definitions and parameters
- **Strategy Manager** — evaluates strategies on market data
- **Backtest Engine** — simulates trades via `simulate_trade()`
- **Performance Analyzer** — computes detailed metrics for each parameter set
- **WalkForwardOptimizer** — can be used in conjunction for robustness testing

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      ParameterOptimizer                         │
│                                                                  │
│  optimize_grid()  │  optimize_random()  │  evaluate_parameters()│
│  rank_results()   │  summary()          │  export_csv()         │
│  to_dict()        │                                              │
└──────────┬─────────┴─────────────┬──────────────────────────────┘
           │                       │
           ▼                       ▼
    ┌──────────────┐      ┌──────────────┐
    │  Grid        │      │  Random      │
    │  Generator   │      │  Sampler     │
    └──────┬───────┘      └──────┬───────┘
           │                     │
           └─────────┬───────────┘
                     ▼
          ┌─────────────────────┐
          │  Parameter          │
          │  Injection          │
          │  (temporary)        │
          └─────────┬───────────┘
                    ▼
          ┌─────────────────────┐
          │  Strategy           │
          │  .generate_signal() │
          └─────────┬───────────┘
                    ▼
          ┌─────────────────────┐
          │  simulate_trade()   │
          │  (from backtest)    │
          └─────────┬───────────┘
                    ▼
          ┌─────────────────────┐
          │  PerformanceAnalyzer│
          │  (metrics)          │
          └─────────┬───────────┘
                    ▼
          ┌─────────────────────┐
          │  ParameterResult    │
          │  stored + ranked    │
          └─────────────────────┘
```

### Workflow

1. **Capture**: The optimizer captures the strategy's public attributes as the "original parameters".
2. **Generate**: Grid search builds a cartesian product of all parameter values; random search samples from ranges.
3. **Inject**: Each combination is temporarily set onto the strategy instance.
4. **Backtest**: The strategy runs on historical data; BUY signals are executed via `simulate_trade()`.
5. **Analyse**: Trade history is passed to `PerformanceAnalyzer` for full metrics.
6. **Restore**: Original parameters are restored before the next combination.
7. **Rank**: All results are sorted by the chosen metric (default: `net_profit`).
8. **Export**: Results can be exported to CSV or serialised as JSON.

---

## Search Methods

### Grid Search

Exhaustively evaluates all combinations from a parameter grid:

```python
grid = {
    "rsi_period": [10, 14, 20],       # 3 values
    "stop_loss_pct": [0.03, 0.05],    # 2 values
}
# Total: 3 × 2 = 6 combinations
```

Each list in the grid is a discrete set of values. The cartesian product produces all combinations.

### Random Search

Randomly samples parameter combinations from ranges:

```python
ranges = {
    "rsi_period": (10, 20),             # integer range → random.randint
    "stop_loss_pct": (0.01, 0.10),      # float range → random.uniform
    "rsi_buy_threshold": [50, 55, 60],  # list → random.choice
    "fixed_param": 42,                  # scalar → used as-is
}
```

Three range types are supported:

| Range Spec | Type | Sampling |
| :--- | :--- | :--- |
| `(low, high)` with `int` bounds | Integer | `random.randint(low, high)` |
| `(low, high)` with `float` bounds | Float | `random.uniform(low, high)` |
| `[v1, v2, ...]` | Discrete | `random.choice(list)` |
| Scalar value | Constant | Used as-is for every iteration |

---

## Parameter Injection

The optimizer **never permanently modifies** a strategy. For each parameter combination:

1. `_capture_params()` — reads all public, non-method attributes via `dir(strategy)` and stores them
2. `_inject_params(params)` — sets `setattr(strategy, key, value)` for each parameter
3. The strategy runs the backtest with the injected parameters
4. `_restore_params()` — restores every original value via `setattr`

This means the same `ParameterOptimizer` instance can be reused across multiple optimisation runs without strategy state corruption.

---

## Supported Metrics

| Metric | Source Field | Description |
| :--- | :--- | :--- |
| `net_profit` | `StrategyPerformance.net_profit` | Net profit in NPR |
| `return_pct` | `StrategyPerformance.return_pct` | Total portfolio return % |
| `profit_factor` | `StrategyPerformance.profit_factor` | Gross profit / gross loss |
| `win_rate` | `StrategyPerformance.win_rate` | Winning trades % |
| `expectancy` | `StrategyPerformance.expectancy` | Mean net profit per trade |
| `sharpe_ratio` | `StrategyPerformance.sharpe_ratio` | Annualised Sharpe ratio |
| `sortino_ratio` | `StrategyPerformance.sortino_ratio` | Annualised Sortino ratio |
| `calmar_ratio` | `StrategyPerformance.calmar_ratio` | Return / max drawdown |
| `max_drawdown` | `StrategyPerformance.max_drawdown` | Maximum drawdown % |
| `recovery_factor` | `StrategyPerformance.recovery_factor` | Net profit / drawdown |
| `score` | `StrategyPerformance.score` | Composite quality score (0–100) |

---

## Dataclasses

### ParameterResult

| Field | Type | Description |
| :--- | :--- | :--- |
| `parameters` | `dict[str, Any]` | The parameter combination evaluated |
| `net_profit` | `float` | Net profit in NPR |
| `return_pct` | `float` | Total portfolio return % |
| `win_rate` | `float` | Win rate % |
| `profit_factor` | `float` | Profit factor |
| `drawdown` | `float` | Max drawdown % |
| `score` | `float` | Composite quality score |
| `metrics` | `dict[str, Any]` | Full `StrategyPerformance.to_dict()` output |

### OptimizationResult

| Field | Type | Description |
| :--- | :--- | :--- |
| `strategy_name` | `str` | Name of the optimised strategy |
| `best_parameters` | `dict[str, Any]` | Parameters that achieved the best metric |
| `best_metric` | `float` | Best metric value |
| `metric_name` | `str` | The metric used for ranking |
| `results` | `list[ParameterResult]` | All evaluated combinations, sorted |
| `total_combinations` | `int` | Total possible combinations (grid search) |
| `tested_combinations` | `int` | Combinations actually evaluated |
| `runtime_seconds` | `float` | Wall-clock duration |
| `ranking` | `list[dict]` | Abbreviated top-20 ranking |

---

## Usage Examples

### 1. Grid Search — Momentum Strategy

```python
from src.strategies.momentum import MomentumStrategy
from src.optimization.parameter_optimizer import (
    ParameterOptimizer,
)
import pandas as pd

# Load data
df = pd.read_csv("data/raw/nabbc.csv", index_col=0, parse_dates=True)

# Create optimizer
strategy = MomentumStrategy()
optimizer = ParameterOptimizer(strategy)

# Grid search over 6 combinations
result = optimizer.optimize_grid(
    historical_data=df,
    parameter_grid={
        "rsi_period": [10, 14, 20],
        "stop_loss_pct": [0.03, 0.05],
    },
    initial_capital=1_000_000,
    metric="score",  # optimise for composite quality score
)

print(f"Best parameters: {result.best_parameters}")
print(f"Best score: {result.best_metric:.2f}")
print(f"Total tested: {result.tested_combinations}")
print(f"Runtime: {result.runtime_seconds:.2f}s")
print(f"Rankings:")
for entry in result.ranking[:5]:
    print(f"  #{entry['rank']}: {entry['parameters']} → {entry['score']:.2f}")
```

### 2. Random Search — Breakout Strategy

```python
from src.strategies.breakout import BreakoutStrategy

strategy = BreakoutStrategy()
optimizer = ParameterOptimizer(strategy)

result = optimizer.optimize_random(
    historical_data=df,
    parameter_ranges={
        "lookback_period": (10, 30),
        "volume_threshold": (1.0, 2.0),
        "stop_loss_pct": (0.02, 0.08),
        "target_pct": (0.08, 0.20),
    },
    iterations=50,
    initial_capital=1_000_000,
    metric="profit_factor",
    random_seed=42,  # reproducible
)

print(f"Best: {result.best_parameters} → PF={result.best_metric:.2f}")
```

### 3. Evaluate a Single Parameter Set

```python
metrics = optimizer.evaluate_parameters(
    parameters={"rsi_period": 20, "stop_loss_pct": 0.04},
    historical_data=df,
    initial_capital=1_000_000,
)
print(f"Net profit: {metrics['net_profit']:,.2f}")
print(f"Win rate: {metrics['win_rate']:.1f}%")
print(f"Score: {metrics['score']:.1f}")
```

### 4. Rank and Export Results

```python
# Rank by a different metric after optimisation
ranked = optimizer.rank_results(metric="profit_factor")
for r in ranked[:3]:
    print(f"{r.parameters} → PF={r.profit_factor:.2f}")

# Export all results to CSV
optimizer.export_csv("optimization_results.csv")

# Get full JSON-serialisable dict
data = optimizer.to_dict()
```

### 5. Summary Report

```python
summary = optimizer.summary()
print(f"Best score: {summary['best_score']:.1f}")
print(f"Worst score: {summary['worst_score']:.1f}")
print(f"Average net profit: {summary['average_net_profit']:,.2f}")
print(f"Top 3 combinations:")
for entry in summary["top_10"][:3]:
    print(f"  {entry['parameters']} → score={entry['score']:.1f}")
```

---

## Example Output

### OptimizationResult.to_dict()

```json
{
    "strategy_name": "MomentumStrategy",
    "best_parameters": {
        "rsi_period": 20,
        "stop_loss_pct": 0.05
    },
    "best_metric": 82.3,
    "metric_name": "score",
    "results": [
        {
            "parameters": {"rsi_period": 20, "stop_loss_pct": 0.05},
            "net_profit": 125000.0,
            "return_pct": 12.5,
            "win_rate": 68.0,
            "profit_factor": 2.8,
            "drawdown": 4.5,
            "score": 82.3
        },
        {
            "parameters": {"rsi_period": 14, "stop_loss_pct": 0.03},
            "net_profit": 98000.0,
            "return_pct": 9.8,
            "win_rate": 62.0,
            "profit_factor": 2.1,
            "drawdown": 5.2,
            "score": 74.1
        }
    ],
    "total_combinations": 6,
    "tested_combinations": 6,
    "runtime_seconds": 15.23,
    "ranking": [
        {"rank": 1, "parameters": {"rsi_period": 20, "stop_loss_pct": 0.05}, "score": 82.3},
        {"rank": 2, "parameters": {"rsi_period": 14, "stop_loss_pct": 0.05}, "score": 78.9}
    ]
}
```

### CSV Export

```csv
rsi_period,stop_loss_pct,net_profit,return_pct,win_rate,profit_factor,drawdown,score
20,0.05,125000.0,12.5,68.0,2.8,4.5,82.3
14,0.03,98000.0,9.8,62.0,2.1,5.2,74.1
...
```

---

## Error Handling

| Scenario | Behaviour |
| :--- | :--- |
| **Empty parameter grid** | `ValueError` raised |
| **Invalid metric name** | `ValueError` with list of valid metrics |
| **Strategy failure on a combination** | Exception caught; `ParameterResult` returned with zeroed metrics |
| **Small DataFrame** | Backtest produces no trades; zeroed metrics returned |
| **Empty results export** | CSV file created with headers only |
| **Invalid range types** | `ValueError` in random search |
| **Negative iterations** | `ValueError` raised |

---

## Performance Considerations

| Factor | Impact |
| :--- | :--- |
| **Grid size** | Grows exponentially with number of parameters. A 5-parameter grid with 5 values each = 3125 combinations |
| **DataFrame size** | Larger DataFrames take longer per backtest. Use representative windows rather than full datasets |
| **Strategy complexity** | Complex strategies with heavy indicator computations increase per-iteration time |
| **Recommendation** | Start with random search (50–200 iterations) before running full grid search |

### Optimisation Tips

1. **Use a representative window** — Optimise on 6–12 months of data rather than years
2. **Start with random search** — Identify promising regions, then refine with grid search
3. **Validate with walk-forward** — Run `WalkForwardOptimizer` on the best parameters to check robustness
4. **Avoid sharp ratio with few trades** — Sharpe requires ≥2 trades; use profit_factor or score for sparse backtests

---

## Future Bayesian Optimizer Integration

The architecture is designed for Bayesian optimisation to be added as a third search method:

```python
class ParameterOptimizer:
    def optimize_bayesian(
        self,
        historical_data,
        parameter_bounds,
        initial_capital,
        metric="net_profit",
        iterations=50,
        initial_points=10,
    ):
        # TODO: Implement Bayesian optimisation using scikit-optimize
        # or a similar library.  The _evaluate_combination method
        # provides the objective function interface.
        raise NotImplementedError
```

The `_evaluate_combination` method serves as the **objective function** — it takes parameters, returns a metric value. Bayesian optimisers require exactly this interface.

To add Bayesian support without changing the existing API:

1. Add an `optimize_bayesian()` method
2. Use the result's `metric_name` as the objective
3. Leverage existing `_evaluate_combination` as the surrogate function target
4. Return the same `OptimizationResult` format for API consistency
