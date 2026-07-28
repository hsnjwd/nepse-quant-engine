# Monte Carlo Simulation Engine

## Architecture

```
┌─────────────────────┐     ┌───────────────────────────┐
│  Trade History       │────▶│  MonteCarloSimulator       │
│  (list[dict])        │     │                           │
│  net_profit          │     │  ┌─────────────────────┐  │
│  return_pct          │     │  │ simulate_bootstrap() │  │
│  holding_days        │     │  │ (resample w/ repl.) │  │
│                      │     │  ├─────────────────────┤  │
│                      │     │  │ simulate_shuffle()   │  │
│                      │     │  │ (permute order)      │  │
│                      │     │  ├─────────────────────┤  │
│                      │     │  │ simulate_returns()   │  │
│                      │     │  │ (compound returns)   │  │
│                      │     │  └─────────────────────┘  │
│                      │     │           │               │
│                      │     │           ▼               │
│                      │     │  ┌─────────────────────┐  │
│                      │     │  │ summary()            │  │
│                      │     │  │ → MonteCarloSummary  │  │
│                      │     │  └─────────────────────┘  │
│                      │     │           │               │
│                      │     │  ┌─────────────────────┐  │
│                      │     │  │ export_csv()         │  │
│                      │     │  │ to_dict()            │  │
│                      │     │  └─────────────────────┘  │
└─────────────────────┘     └───────────────────────────┘
```

**Module location**: `src/simulation/monte_carlo.py`

**Dependencies**:
- Python stdlib: `random`, `csv`, `math`, `dataclasses`, `statistics`
- Project logger: `src.logging.logger`

**Integration points**:
- Consumes trade history from `src.backtest.engine.BacktestEngine` (list of `TradeRecord.to_dict()` dicts)
- Metrics follow conventions from `src.backtest.metrics` (`net_profit`, `return_pct`, `holding_days`)
- Designed for use alongside `src.analytics.performance.PerformanceAnalyzer`

---

## Workflow

```
1. Obtain trade history from a backtest
       │
       ▼
2. Create MonteCarloSimulator
   (simulations, confidence_level, random_seed)
       │
       ▼
3. Run one or more simulation methods:
   ├── simulate_bootstrap(trades)   ← resample with replacement
   ├── simulate_shuffle(trades)     ← permute order
   └── simulate_returns(trades)     ← compound return pcts
       │
       ▼
4. summary(results)  →  MonteCarloSummary
       │
       ▼
5. Analyse risk metrics:
   ├── probability_of_profit / loss / ruin
   ├── VaR (value_at_risk_95)
   ├── CVaR (conditional_var_95)
   ├── percentiles (p5–p99)
   └── confidence_interval
       │
       ▼
6. Export:  to_dict() | export_csv(path)
```

---

## Simulation Methods

### Bootstrap (`simulate_bootstrap`)

Randomly samples trades **with replacement** from the original list. Each simulation run draws exactly `N` trades (where `N = len(trades)`), allowing the same trade to appear multiple times.

**When to use**: Assess the distribution of possible outcomes assuming the strategy's trade characteristics (win rate, average win/loss) remain stable but the exact sequence is random.

**Properties**:
- Preserves the original trade pool distribution
- Each simulation may have a different total net profit
- Best for estimating confidence intervals and VaR

```
Original:  [A, B, C, D, E]
Sim 1:     [B, A, C, E, E]
Sim 2:     [C, C, D, D, A]
```

### Shuffle (`simulate_shuffle`)

Randomly permutes the trade order. Each simulation is a different ordering of the **same** set of trades — every trade appears exactly once.

**When to use**: Test whether the strategy's results are sensitive to trade ordering (e.g., streaks of consecutive losses).

**Properties**:
- The total net profit is identical for every simulation
- Drawdown, return_pct, and other sequence-dependent metrics vary
- Useful for isolating sequence risk from outcome risk

```
Original:  [A, B, C, D, E]
Sim 1:     [E, C, A, D, B]
Sim 2:     [B, D, E, A, C]
```

### Return Compounding (`simulate_returns`)

Samples `return_pct` values with replacement and compounds them into an equity curve using:

```
equity_{i+1} = equity_i + equity_i × return_pct_i / 100
```

Starting from an initial equity of `100.0`.

**When to use**: Model the effect of compounding returns, which better reflects real portfolio growth where gains and losses compound.

**Properties**:
- Uses percentage returns rather than raw net profits
- Captures compounding effects (asymmetric impact of losses)
- Higher variance than bootstrap for volatile strategies

---

## Dataclasses

### `SimulationResult`

| Field | Type | Description |
|-------|------|-------------|
| `simulation_number` | `int` | 1-based simulation index |
| `ending_equity` | `float` | Final cumulative P&L |
| `net_profit` | `float` | Sum of all trade net profits |
| `max_drawdown` | `float` | Max peak-to-trough decline (%) |
| `return_pct` | `float` | Return from peak equity (%) |
| `win_rate` | `float` | Percentage of profitable trades |
| `profit_factor` | `float` | Gross profit / abs(gross loss) |

### `MonteCarloSummary`

| Field | Type | Description |
|-------|------|-------------|
| `simulations` | `int` | Number of simulation runs |
| `mean_return` | `float` | Mean ending equity |
| `median_return` | `float` | Median ending equity |
| `best_return` | `float` | Highest ending equity |
| `worst_return` | `float` | Lowest ending equity |
| `mean_drawdown` | `float` | Mean max drawdown (%) |
| `max_drawdown` | `float` | Worst max drawdown (%) |
| `probability_of_profit` | `float` | % of sims ending > 0 |
| `probability_of_loss` | `float` | % of sims ending < 0 |
| `probability_of_ruin` | `float` | % of sims with drawdown > 50% |
| `value_at_risk_95` | `float` | 5th percentile ending equity |
| `conditional_var_95` | `float` | Mean of worst 5% equities |
| `confidence_interval` | `dict` | Lower/upper bounds at configured level |
| `percentiles` | `dict` | p5, p10, p25, p50, p75, p90, p95, p99 |
| `best_equity` | `list[float]` | Best simulation equity curve |
| `worst_equity` | `list[float]` | Worst simulation equity curve |
| `average_equity` | `list[float]` | Pointwise average equity curve |
| `equity_curves` | `list[list[float]]` | All equity curves |

---

## Risk Metrics Explained

### Value at Risk (VaR)

The 5th percentile of ending equity — the maximum loss that will not be exceeded with 95% confidence.

```
VaR_95 = percentile(sorted_equities, 5)
```

**Interpretation**: "There is a 5% chance of ending equity being below VaR_95."

### Conditional VaR (Expected Shortfall)

The mean ending equity of the worst 5% of simulations. Captures the **severity** of tail losses when VaR is breached.

```
CVaR_95 = mean(equities <= VaR_95)
```

**Interpretation**: "When losses exceed VaR, the average loss is CVaR_95."

### Probability of Ruin

The percentage of simulations whose equity curve experienced a drawdown exceeding 50% of peak equity. A standard metric for assessing catastrophic risk.

```
ruin_count = count(simulations where max_drawdown > 50%)
probability_of_ruin = ruin_count / total_simulations × 100
```

### Confidence Interval

Calculated from the sorted ending equities at the configured confidence level (default: 95%):

```
lower_idx = N × (1 - confidence) / 2
upper_idx = N × (1 + confidence) / 2 - 1
```

**Interpretation**: "With [confidence]% certainty, ending equity falls between the lower and upper bounds."

### Percentiles

Interpolated percentiles provide a complete picture of the outcome distribution:

- **p5**: 5th percentile (95% of outcomes are better)
- **p25**: 25th percentile (75% of outcomes are better)
- **p50**: Median (50th percentile)
- **p95**: 95th percentile (only 5% of outcomes are better)
- **p99**: 99th percentile (top 1% outcome)

---

## Usage Examples

### Basic example

```python
from src.simulation.monte_carlo import MonteCarloSimulator

# Trade history from a backtest
trades = [
    {"net_profit": 1000.0, "return_pct": 2.0, "holding_days": 5},
    {"net_profit": -400.0, "return_pct": -0.8, "holding_days": 3},
    {"net_profit": 1500.0, "return_pct": 3.0, "holding_days": 7},
    {"net_profit": 200.0, "return_pct": 0.4, "holding_days": 2},
    {"net_profit": -100.0, "return_pct": -0.2, "holding_days": 4},
    {"net_profit": 3000.0, "return_pct": 6.0, "holding_days": 10},
]

simulator = MonteCarloSimulator(simulations=10_000, random_seed=42)
results = simulator.simulate_bootstrap(trades)
summary = simulator.summary(results)

print(summary.to_dict())
```

### Comparing methods

```python
bootstrap = simulator.simulate_bootstrap(trades)
shuffle = simulator.simulate_shuffle(trades)
returns = simulator.simulate_returns(trades)

bs_summary = simulator.summary(bootstrap)
sh_summary = simulator.summary(shuffle)
rt_summary = simulator.summary(returns)
```

### Exporting results

```python
simulator.export_csv("/path/to/results.csv", results)
```

### Reproducible runs

```python
sim_a = MonteCarloSimulator(simulations=10_000, random_seed=42)
sim_b = MonteCarloSimulator(simulations=10_000, random_seed=42)
# sim_a and sim_b produce identical results
```

---

## API Reference

### `MonteCarloSimulator`

#### Constructor

```python
MonteCarloSimulator(
    simulations: int = 10_000,
    confidence_level: float = 0.95,
    random_seed: int | None = None,
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `simulations` | `10000` | Number of Monte Carlo runs |
| `confidence_level` | `0.95` | Confidence level (0–1) for interval estimation |
| `random_seed` | `None` | Optional seed for reproducibility |

**Raises**: `ValueError` if `simulations ≤ 0` or `confidence_level ∉ (0, 1]`.

#### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `simulate_bootstrap(trades)` | `list[SimulationResult]` | Resample with replacement |
| `simulate_shuffle(trades)` | `list[SimulationResult]` | Randomly permute order |
| `simulate_returns(trades)` | `list[SimulationResult]` | Compound return percentages |
| `summary(results)` | `MonteCarloSummary` | Aggregate risk metrics |
| `export_csv(path, results)` | `None` | Export to CSV file |

---

## Example Output

```json
{
  "simulations": 10000,
  "mean_return": 5250.50,
  "median_return": 4900.00,
  "best_return": 18500.00,
  "worst_return": -3200.00,
  "mean_drawdown": 8.35,
  "max_drawdown": 42.10,
  "probability_of_profit": 87.30,
  "probability_of_loss": 12.40,
  "probability_of_ruin": 1.20,
  "value_at_risk_95": -1200.00,
  "conditional_var_95": -1850.00,
  "confidence_interval": {
    "lower": -800.00,
    "upper": 12500.00
  },
  "percentiles": {
    "p5": -1200.00,
    "p10": -400.00,
    "p25": 1800.00,
    "p50": 4900.00,
    "p75": 8200.00,
    "p90": 11200.00,
    "p95": 13800.00,
    "p99": 17000.00
  }
}
```

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Empty trade list | `ValueError` with descriptive message |
| Zero or negative simulations | `ValueError` on construction |
| Confidence level ≤ 0 or > 1 | `ValueError` on construction |
| Missing keys in trade dict | Defaults to `0.0` |
| Single trade | Works correctly (all simulations identical) |
| All zero net profits | Zero metrics returned |
| Empty results for summary | `ValueError` with descriptive message |
| Empty results for CSV export | `ValueError` with descriptive message |

---

## Performance Considerations

- **Large simulations**: 10,000 runs × 100 trades = 1 million iterations; memory for equity curves scales with `simulations × trade_count`.
- **Equity curve storage**: `MonteCarloSummary.equity_curves` can consume significant memory. For very large simulations, set `equity_curves` to empty before serialisation.
- **Random seed**: Using a fixed seed enables deterministic testing but may reduce independence across runs for hyperparameter optimisation.
- **Logging**: Progress is logged every 10% of simulations via `logger.debug()`. Disable with the project's logging configuration.

### Future Optimisations

- **Multiprocessing**: The simulation loop is trivially parallelisable. Each simulation is independent.
- **Vectorisation**: For `simulate_returns`, NumPy vectorisation would provide 10–100× speedup.
- **Memory-mapped equity curves**: For very large simulations (1M+), equity curves can be written to disk instead of RAM.

---

## Limitations

1. **Stationarity assumption**: Bootstrap resampling assumes past trade characteristics are representative of future outcomes.
2. **Independence**: Shuffle assumes trade outcomes are independent (ignores autocorrelation).
3. **No market regime modelling**: The engine does not simulate changing market conditions.
4. **Point estimates**: VaR and CVaR are point estimates with inherent sampling error.
5. **Equity curve approximation**: The simplified equity curves in MonteCarloSummary use only two points (`[0, ending_equity]`). For detailed per-trade curves, store them during simulation.

---

## Future Improvements

1. **GPU acceleration** via CuPy or JAX for 100,000+ simulations
2. **Multiprocessing** pool execution for CPU-bound parallelisation
3. **Copula-based joint simulation** of multiple correlated strategies
4. **Regime-switching** Monte Carlo that conditions on market state
5. **Bayesian bootstrap** using Dirichlet distribution weights
6. **Realised equity curve storage** for full per-trace visualisation
7. **Web UI integration** with Plotly/D3 visualisation of outcome distributions
