# Performance Analytics

`src/backtesting/statistics.py` provides institutional-grade analytics
beyond the legacy metrics module. The `AdvancedPerformanceAnalyzer`
consumes the equity curve, per-period returns, closed trades, optional
benchmark returns, and an exposure series to produce an
`AdvancedPerformanceReport`.

## Headline metrics

| Metric | Definition |
|---|---|
| Total return | `equity[-1] / equity[0] − 1` |
| Annualized return | CAGR over the run length |
| Volatility | Annualised standard deviation of period returns |
| Sharpe | `mean(excess) / stdev(excess) × √periods` |
| Sortino | Uses downside deviation (semi-variance) |
| Calmar | Annualized return ÷ max drawdown |
| Max drawdown | Largest peak-to-trough, percentage |
| Recovery factor | Total return ÷ drawdown depth |
| MAR ratio | Annualized return % ÷ max drawdown % |
| Ulcer index | √mean(drawdown depth²) |
| Omega | Probability-weighted gain/loss vs threshold |
| Gain/loss ratio | Mean gain ÷ mean loss |
| SQN | `mean / stdev × √n` |
| Expectancy | Mean period return |
| Alpha / Beta | Regression vs benchmark (annualised alpha) |

## Trade-level analytics

- Win rate, profit factor, average trade return, average holding bars
- Holding-period distribution histogram
- Exposure time (fraction of bars with exposure above a threshold)
- Trade count and per-symbol breakdown (`BacktestResult.symbol_trades`)

## Rolling series

- `rolling_sharpe(returns, window, periods)`
- `rolling_sortino(returns, window, periods)`
- `rolling_drawdown(equity, window)`

## Monthly returns

`monthly_returns_table(equity, timestamps)` builds a pivot table of
monthly returns suitable for heatmaps.

## Usage

```python
from src.backtesting.statistics import AdvancedPerformanceAnalyzer

analyzer = AdvancedPerformanceAnalyzer(
    periods_per_year=252, risk_free_rate=0.0, rolling_window=30,
)
report = analyzer.analyze(
    equity=result.equity_curve,
    returns=result.returns,
    trades=result.trades,
    benchmark_returns=result.benchmark_returns,
    exposure=[s.exposure for s in result.portfolio_snapshots],
)
print(report.sharpe, report.max_drawdown, report.ulcer_index)
report.to_dict()  # JSON-serialisable headline metrics
```

## Caveats

- Trades record exit-side costs; entry-side commission is reflected in
  the equity curve but not in `Trade.net_pnl` (documented limitation).
- Partial position reductions do not emit standalone trades; only full
  closes (and reversals) produce `Trade` records.
