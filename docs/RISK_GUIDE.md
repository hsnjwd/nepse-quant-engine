# Risk Guide

The Risk Lab (`src/risk/lab.py`) provides Monte Carlo driven risk
measurement for the NEPSE Quant Engine: Value-at-Risk, Expected
Shortfall, stress testing, ruin probability, drawdown probability, and
recovery analysis.

## Quick Start

```python
from src.risk.lab import RiskLab

lab = RiskLab(
    returns=df["return"],      # Series/array of daily returns (decimal)
    portfolio_value=1_000_000,
    seed=42,
)

var = lab.var(confidence=0.95, method="historical")
print(f"VaR: {var.var:.2f}  CVaR: {var.cvar:.2f}")

sim = lab.monte_carlo(simulations=10_000, horizon=252)
print(f"P(loss): {sim.probability_of_loss:.1%}")
print(f"P(ruin>50%): {sim.probability_of_ruin:.2%}")
```

## Methods

### Value at Risk

`var(confidence=0.95, method="historical")` → `VaRResult`

| method | Description |
| --- | --- |
| `historical` | Empirical quantile of observed returns |
| `parametric` | Normal-distribution assumption (uses scipy) |
| `monte_carlo` | VaR of 100k simulated returns |

`VaRResult` carries `var`, `cvar` (Expected Shortfall), `std`, and
`notes`.

### Stress Testing

`stress_test()` runs five standard scenarios (market crash −20%, bear
market −10%, flash crash −5%, rally +10%, boom +20%) and returns a list
of `StressTestResult`. `custom_stress(shock_pct, scenario)` applies a
custom shock.

### Monte Carlo

`monte_carlo(simulations=10_000, horizon=252, ruin_threshold_pct=50)` →
`MonteCarloLabResult`:

- `ending_values` — simulated ending portfolio values
- `mean_ending`, `median_ending`, `p5`, `p95`
- `probability_of_loss`
- `probability_of_ruin` (breaching the drawdown threshold)
- `max_drawdown_pct`
- `var_95`, `cvar_95`

### Probability of Ruin & Recovery

- `drawdown_probability(threshold_pct, simulations, horizon)` — chance
  of breaching a drawdown threshold.
- `recovery_analysis(simulations, horizon, target_return_pct)` —
  median/mean recovery days and recovery probability.

## API

The REST endpoints under `/risk/*` accept `returns` (list of decimals),
`portfolio_value`, `confidence`, `method`, `simulations`, `horizon`, and
`seed`. See [API_GUIDE.md](API_GUIDE.md).

## Streamlit

The **Risk Dashboard** page (`risk_dashboard_page.py`) and **Monte Carlo
Lab** page (`monte_carlo_lab_page.py`) surface these measurements
interactively with Plotly charts.

## Caveats

- Estimates are only as good as the returns history — the lab logs a
  warning below 20 observations.
- Parametric VaR assumes normality, which understates tail risk.
- Use half-Kelly / conservative risk fractions in live trading (see
  `src/optimization/kelly.py`).
