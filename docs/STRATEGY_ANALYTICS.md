# Strategy Performance Analytics Module

## Overview

The Strategy Performance Analytics module provides a quantitative framework for analysing completed backtest results. It computes detailed performance statistics for individual strategies and produces cross-strategy rankings.

This module is **purely analytical** — it does NOT perform any trading, signal generation, or backtesting. It only analyses existing backtest output produced by the `BacktestEngine`.

---

## Architecture

```
┌─────────────────────┐     ┌──────────────────────┐
│   BacktestEngine    │────>│  Trade History Data   │
│  (produces trades)  │     │  (list[dict])         │
└─────────────────────┘     └──────────┬───────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────┐
│              PerformanceAnalyzer                  │
│                                                   │
│  analyze(strategy_name, symbol, capital, trades)  │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │  StrategyPerformance (dataclass)            │  │
│  │  • Basic stats (win rate, PF, expectancy)   │  │
│  │  • Risk-adjusted ratios (Sharpe, Sortino)   │  │
│  │  • Quality score + rating                   │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│              StrategyRanking                      │
│                                                   │
│  rank([StrategyPerformance, ...])                 │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │  RankEntry (dataclass)                      │  │
│  │  • rank, strategy_name, score, rating       │  │
│  │  • win_rate, profit_factor, net_profit      │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### Module Structure

| Module | Responsibility | Primary Symbols |
| :--- | :--- | :--- |
| `performance.py` | Trade history analysis, metric computation, scoring & ranking | `PerformanceAnalyzer`, `StrategyRanking`, `StrategyPerformance`, `RankEntry` |

---

## Input Data Format

The module accepts trade history in the format produced by `BacktestEngine.run_backtest()`.

### Required Trade Fields

Each entry in `trade_history` should be a dictionary containing:

| Field | Type | Description |
| :--- | :--- | :--- |
| `net_profit` | `float` | Net profit/loss in NPR (positive = win, negative = loss). Missing values default to `0.0`. |
| `return_pct` | `float` | Percentage return for the trade. Used for risk-adjusted ratios. |
| `holding_days` | `float` | Duration the trade was open in days/candles. |

### Example Trade Record

```python
{
    "entry_price": 950.0,
    "exit_price": 1020.0,
    "shares": 100,
    "gross_profit": 7000.0,
    "commission": 197.0,
    "net_profit": 6803.0,      # ← consumed by analytics
    "return_pct": 7.16,        # ← consumed by analytics
    "holding_days": 5,         # ← consumed by analytics
    "exit_reason": "TARGET",
    "result": "WIN",
    "date": "2024-06-15",
    "symbol": "NABIL",
}
```

---

## Calculation Formulas

### Basic Statistics

| Metric | Formula | Notes |
| :--- | :--- | :--- |
| **Total Trades** | `len(trade_history)` | |
| **Winning Trades** | `count(net_profit > 0)` | |
| **Losing Trades** | `count(net_profit < 0)` | |
| **Breakeven Trades** | `count(net_profit == 0)` | |
| **Win Rate** | `(winning_trades / total_trades) × 100` | Percentage (0–100) |
| **Gross Profit** | `∑ net_profit where net_profit > 0` | NPR |
| **Gross Loss** | `\|∑ net_profit where net_profit < 0\|` | NPR (always positive) |
| **Net Profit** | `gross_profit - gross_loss` | NPR |
| **Average Win** | `mean(net_profit where net_profit > 0)` | NPR |
| **Average Loss** | `mean(net_profit where net_profit < 0)` | NPR (negative) |
| **Largest Win** | `max(net_profit)` | NPR |
| **Largest Loss** | `min(net_profit)` | NPR (negative) |
| **Average Return** | `mean(return_pct)` | Percentage |
| **Average Holding** | `mean(holding_days)` | Days |
| **Expectancy** | `mean(net_profit)` | NPR per trade |

### Profit Factor

```
Profit Factor = Gross Profit / Gross Loss
```

- `0.0` when there are no losing trades (gross_loss == 0)
- A PF > 1.0 means the strategy makes more than it loses
- Capped at 5.0 for scoring purposes

### Maximum Drawdown

The equity curve is simulated by starting with `starting_capital` and adding each trade's `net_profit` sequentially:

```
equity[0] = starting_capital
equity[i] = equity[i-1] + trade[i-1].net_profit    # for i >= 1
```

Then drawdown is computed as:

```
peak[i] = max(equity[0..i])
drawdown[i] = (peak[i] - equity[i]) / peak[i] × 100
max_drawdown = max(drawdown)
```

### Recovery Factor

```
Recovery Factor = Net Profit / |Max Drawdown in NPR|
```

Measures how well the strategy recovers from its worst peak-to-trough decline.

### Risk-Adjusted Ratios

#### Sharpe Ratio

```
Sharpe = (mean(return_pct) / population_std(return_pct)) × √252
```

- Uses **population standard deviation** (pstdev, divisor = n)
- Annualised with `√252` (trading days per year)
- Returns `None` when fewer than 2 trades exist or standard deviation is zero

#### Sortino Ratio

```
Sortino = (mean(return_pct) / downside_std) × √252
```

Where `downside_std` is the semi-deviation from zero (square root of the mean of squared negative returns). This penalises only the **downside volatility**.

$$\text{downside\_std} = \sqrt{\frac{1}{n} \sum_{i=1}^{n} \min(0, r_i)^2}$$

- Returns `None` when fewer than 2 trades exist
- Returns `None` when downside deviation is zero (all returns are non-negative)

#### Calmar Ratio

```
Calmar = Total Return % / Max Drawdown %
```

- Measures return per unit of drawdown risk
- Returns `None` when max drawdown is zero

### Ending Capital & Total Return

```
ending_capital = equity_curve[-1]
return_pct = (ending_capital - starting_capital) / starting_capital × 100
```

---

## Composite Quality Score

The composite score combines five weighted sub-scores into a 0–100 scale:

| Component | Weight | Normalisation | Full Marks At |
| :--- | :--- | :--- | :--- |
| Win Rate | 25 pts | `(win_rate / 100) × 25` | 100% win rate |
| Profit Factor | 25 pts | `(min(PF, 5.0) / 5.0) × 25` | PF ≥ 5.0 |
| Expectancy | 20 pts | `(min(max(exp_ratio, 0), 0.02) / 0.02) × 20` | 2% expectancy/capital |
| Drawdown | 15 pts | `(1 - min(max(DD/30, 0), 1)) × 15` | 0% drawdown (>30% = 0) |
| Sharpe Ratio | 15 pts | `(min(max(SR, 0), 3.0) / 3.0) × 15` | SR ≥ 3.0 |

Each component is clamped between 0 and its maximum before summing. The final score is clamped to [0, 100].

---

## Rating System

| Score Range | Rating | Description |
| :--- | :--- | :--- |
| 95–100 | **Elite** | Exceptional risk-adjusted performance |
| 85–94 | **Excellent** | Strong across all metrics |
| 70–84 | **Good** | Solid, above-average strategy |
| 55–69 | **Average** | Mediocre, room for improvement |
| 40–54 | **Weak** | Below-average performance |
| < 40 | **Poor** | Significant issues in multiple areas |

---

## Strategy Ranking

Strategies are ranked by composite score in descending order. The `StrategyRanking.rank()` method produces a list of `RankEntry` objects, each containing:

| Field | Description |
| :--- | :--- |
| `rank` | 1-based ordinal position |
| `strategy_name` | Name of the strategy |
| `score` | Composite quality score (0–100) |
| `rating` | Qualitative rating label |
| `win_rate` | Win rate percentage |
| `profit_factor` | Profit factor |
| `net_profit` | Net profit in NPR |

---

## Usage Examples

### 1. Analysing a Single Strategy

```python
from src.analytics.performance import PerformanceAnalyzer

analyzer = PerformanceAnalyzer()

# Trade history from a backtest run
trades = results["trades"]  # from backtest engine output

perf = analyzer.analyze(
    strategy_name="MomentumStrategy",
    symbol="NABIL",
    starting_capital=1_000_000,
    trade_history=trades,
)

print(f"Score : {perf.score:.1f} ({perf.rating})")
print(f"Win Rate: {perf.win_rate:.1f}%")
print(f"Sharpe : {perf.sharpe_ratio:.2f}")
print(f"Max DD : {perf.max_drawdown:.2f}%")
print(f"Net PnL: Rs. {perf.net_profit:,.2f}")
```

### 2. Getting a Dictionary Output

```python
d = analyzer.analyze_to_dict("MomentumStrategy", "NABIL", 1_000_000, trades)
print(d["score"], d["rating"])
```

### 3. Ranking Multiple Strategies

```python
from src.analytics.performance import PerformanceAnalyzer, StrategyRanking

analyzer = PerformanceAnalyzer()

results = []
for backtest_result in all_backtest_results:
    perf = analyzer.analyze(
        strategy_name=backtest_result["symbol"],
        symbol=backtest_result["symbol"],
        starting_capital=1_000_000,
        trade_history=backtest_result["trades"],
    )
    results.append(perf)

ranking = StrategyRanking()
rank_entries = ranking.rank(results)

for entry in rank_entries:
    print(
        f"#{entry.rank} {entry.strategy_name:15s}"
        f"  score={entry.score:5.1f}  {entry.rating:10s}"
        f"  WR={entry.win_rate:5.1f}%  PF={entry.profit_factor:.2f}"
    )

# Or as a list of dicts
dict_ranking = ranking.rank_to_dict(results)
```

### 4. Handling Empty Trade Histories

```python
perf = analyzer.analyze("EmptyStrategy", "NABIL", 1_000_000, [])
# Returns zeroed metrics with ending_capital == starting_capital
print(perf.total_trades)    # 0
print(perf.ending_capital)  # 1_000_000.0
print(perf.metadata)        # {"empty_history": True}
```

---

## Example Output

### StrategyPerformance.to_dict() — Profitable Strategy

```json
{
    "strategy_name": "MomentumStrategy",
    "symbol": "NABIL",
    "total_trades": 50,
    "winning_trades": 32,
    "losing_trades": 16,
    "breakeven_trades": 2,
    "win_rate": 64.0,
    "profit_factor": 2.35,
    "expectancy": 4250.5,
    "gross_profit": 285000.0,
    "gross_loss": 121250.0,
    "net_profit": 163750.0,
    "average_win": 8906.25,
    "average_loss": -7578.12,
    "average_return_pct": 4.25,
    "average_holding_days": 4.8,
    "largest_win": 25000.0,
    "largest_loss": -15000.0,
    "max_drawdown": 8.45,
    "recovery_factor": 3.21,
    "sharpe_ratio": 1.85,
    "sortino_ratio": 2.45,
    "calmar_ratio": 0.72,
    "ending_capital": 1163750.0,
    "return_pct": 16.38,
    "score": 78.5,
    "rating": "Good",
    "metadata": {}
}
```

### RankEntry.to_dict() — Ranking Table

```json
[
    {
        "rank": 1,
        "strategy_name": "MomentumStrategy",
        "score": 85.2,
        "rating": "Excellent",
        "win_rate": 70.0,
        "profit_factor": 3.2,
        "net_profit": 163750.0
    },
    {
        "rank": 2,
        "strategy_name": "BreakoutStrategy",
        "score": 72.3,
        "rating": "Good",
        "win_rate": 62.0,
        "profit_factor": 2.1,
        "net_profit": 98500.0
    }
]
```

---

## Error Handling

The module is designed to be resilient:

| Scenario | Behaviour |
| :--- | :--- |
| **Empty trade history** | Returns a zeroed `StrategyPerformance` with `ending_capital == starting_capital` and `metadata["empty_history"] = True` |
| **Single trade** | Basic metrics computed; risk-adjusted ratios return `None` |
| **Zero starting capital** | `return_pct` returns `0.0` (safe division) |
| **Missing `net_profit`** | Defaults to `0.0` (treated as breakeven) |
| **All winning trades** | Sortino ratio returns `None` (no downside to measure) |
| **No drawdown** | Calmar ratio returns `None` |
| **Unexpected errors** | Caught and logged; returns a zeroed result with `metadata["error"]` |
| **Negative equity** | Handled gracefully; drawdown computed normally |

---

## Future Extensions

1. **Annualised metrics** — Add CAGR, rolling Sharpe, and time-weighted returns when trade dates are available
2. **Monte Carlo simulation** — Generate confidence intervals for key metrics
3. **Benchmark comparison** — Compare strategy returns against NEPSE index benchmarks
4. **Psychological metrics** — Add consecutive wins/losses, average drawdown duration, and profit distribution skewness
5. **Walk-forward analysis** — Segment trades by period and compare in-sample vs out-of-sample performance
6. **Custom scoring weights** — Allow configurable weight profiles for different risk preferences
7. **Trade classification** — Categorise trades by exit reason and compute performance per category
8. **Visualisation helpers** — Generate equity curve data and drawdown series for charting
