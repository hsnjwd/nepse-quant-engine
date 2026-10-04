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

## Platform performance (Sprint 11)

Beyond backtest analytics, the engine ships performance tooling for
the *whole platform* (scanner, analysis, history, API, backtests).

### What was optimised

| Area | Before | After |
|---|---|---|
| Market scan | sequential loop over every CSV | bounded `ThreadPoolExecutor` (`SCANNER_WORKERS`) with deterministic order |
| CSV parsing | re-parsed every file on every scan | cached by file fingerprint (path, mtime, size) with TTL + LRU |
| Indicator pipeline | ~8 `df.copy()` per stock | exactly 1 defensive copy + in-place chaining |
| ATR | temporary `pd.concat` frame per call | element-wise `np.fmax` (identical numerics) |
| History retrieval | fixed 300 s TTL | configurable `HISTORY_CACHE_TTL`; batch retrieval fetches only cache misses |
| Observability | — | `GET /metrics`, request timing middleware, structured `timed()` logs |

### Benchmark suite

`scripts/benchmarks.py` measures, stores and compares results:

```bash
python scripts/benchmarks.py --symbols 50 --rows 500
```

Measures: cold/warm market scan, per-stock analysis, history load
(cache miss / hit / batch), backtest speed, API latency (p95), peak
scan memory (tracemalloc), and subprocess startup. JSON results land
under `benchmarks/results/benchmark_<timestamp>.json`; re-running
prints a before/after comparison against the latest stored run.

### Performance tuning guide

All knobs live in `src/config.py` (env-overridable):

| Variable | Default | Effect |
|---|---|---|
| `SCANNER_WORKERS` | `4` | Scanner thread-pool size; raise on multi-core hosts with fast disks |
| `SCANNER_CACHE_TTL` | `300` | Scanner df/analysis cache lifetime; also drives `market_cache` |
| `SCANNER_CACHE_MAX_ENTRIES` | `600` | LRU bound per cache tier (memory cap; raised from `200` in Sprint 12.3 — the old default was smaller than the 280-file real corpus) |
| `HISTORY_CACHE_TTL` | `300` | `get_history` tiered-cache TTL |
| `SCANNER_LOG_PROGRESS_EVERY` | `25` | Progress-log frequency during scans |
| `ENABLE_PERFORMANCE_MONITORING` | `false` | Turn on DataService metric collection |

Cache policy summary: entries are invalidated when the source file
changes (fingerprint), when the TTL expires, or when indicator
parameters change (analysis keys embed RSI/MACD settings).

## Caveats

- Trades record exit-side costs; entry-side commission is reflected in
  the equity curve but not in `Trade.net_pnl` (documented limitation).
- Partial position reductions do not emit standalone trades; only full
  closes (and reversals) produce `Trade` records.
- Scanner analysis-cache hits return the first scan's `new_alerts` and
  skip `process_alerts` until the cache expires or the CSV changes
  (matches the pre-existing whole-scan `market_cache` behaviour).
