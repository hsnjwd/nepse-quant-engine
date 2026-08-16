# Performance Baseline — Sprint 11.1 / 11.2

Reproducible benchmark suite + measured bottlenecks for the NEPSE Quant Engine.

- **Suite:** `benchmarks/` (`python -m benchmarks.runner --symbols N --rows M [--source synthetic|real] [--workers W] [--seed S]`)
- **Profiler:** `python -m benchmarks.profile_pipeline`
- **Results:** JSON snapshots under `benchmarks/results/runner_*.json` (the `runner_` prefix keeps this suite's snapshots distinct from the older `scripts/benchmarks.py` output)
- **Real-data corpus:** `data/raw/` (~280 scraped NEPSE CSVs). `--source real` selects a deterministic seeded subset (default seed 42) — see `benchmarks/corpus.py`. Synthetic runs never mix frames into a real corpus and vice versa.
- **Legacy wrapper:** `scripts/benchmarks.py` is now a thin compatibility wrapper over `benchmarks/` (deprecated; use `python -m benchmarks.runner`).
- **Environment:** Windows 10, Python 3.12.8, pandas 2.2.3, numpy 2.5.1

> Numbers below are *measured*, never estimated. Synthetic OHLCV data is
> generated deterministically; no production state is modified (alert
> history / portfolio persistence are redirected to temp paths during
> benchmarks — see `benchmarks.pipeline._isolate_state_files`).

---

## 1. Baseline

Baseline captured from the latest clean run (`benchmarks/results/runner_*.json`,
10 synthetic symbols × 300 rows):

| Operation            | Input            | Time (best) | Time (avg) | Notes                          |
| -------------------- | ---------------- | ----------: | ---------: | ------------------------------ |
| Single-stock analysis| 1 symbol, 300 r  |    16.8 ms  |   19.5 ms  | full indicator→signal pipeline |
| History load (cold)  | 10 files, 300 r  |   326.7 ms  |   —        | cache cleared per run          |
| History load (warm)  | 10 files, 300 r  |     7.1 ms  |    7.5 ms  | scanner cache hit              |
| Scan (cold)          | 10 symbols       |   734.0 ms  |   —        | 4 worker threads               |
| Scan (warm)          | 10 symbols       |    58.7 ms  |   67.9 ms  | scanner cache hit              |
| Scan peak memory     | 10 symbols       |    0.05 MiB |   —        | tracemalloc, in-process        |
| Backtest             | 1 symbol, 300 r  |    65.2 ms  |   75.3 ms  | BacktestEngine                 |
| Strategy execution   | 1 symbol, 300 r  |    17.0 ms  |   17.0 ms  | analyze_dataframe (full chain) |
| DataService cache miss| 1 symbol         |    53.9 ms  |   —        | CSVProvider get_history        |
| DataService cache hit | 1 symbol         |     0.17 ms |    0.19 ms | tiered cache hit               |
| API `/analyze`       | 1 request        |   —         |  123.6 ms  | TestClient, p95 329 ms         |
| API `/portfolio`     | 1 request        |   —         |   12.3 ms  | empty portfolio                |

*Repeatability note:* cold-scan / cold-load numbers fluctuate with disk
cache and OS state between runs (±30–60 % observed at this small scale);
warm (cache-hit) numbers are stable. Re-run `benchmarks.runner` several
times and compare warm numbers for regression detection.

## 2. Ranked bottlenecks (measured)

Profile evidence: `benchmarks/profile_pipeline.py` (cProfile).

1. **Pandas indicator internals (rolling/ewm/`__setitem__`)** — dominant
   cost inside `analyze_dataframe` (~63 ms/stock at 500 rows). Location:
   `src/indicators/*.py` + `src/engine/analyzer.py`. This is inherent
   pandas computation, not duplicated work — `analyze_dataframe` already
   performs a single defensive copy (Sprint 11) and all `add_*` steps run
   `inplace=True`. **Status:** measured, no change (no safe win without
   changing indicator values, which is out of scope).
2. **Cold CSV parsing + numeric conversion** — `load_csv` re-parse on
   cold scans. Measured conversion-loop A/B (10 files × 300 rows, 50 warm
   runs): **54.3 ms → 43.0 ms best (~1.26×)** after the Sprint 11.1
   comma-detection fast path. Location: `src/loaders/csv_loader.py`.
   **Status:** optimized (see §3).
3. **Alert-history read-modify-write on cold scans** — `process_alerts`
   → `get_last_state` / `update_state` re-read + rewrite the state file
   per symbol, serialized by a module lock. The file is small (8.7 kB,
   ~0.8 ms load) so impact is minor but shows up in scan profiles when
   the analysis cache is cold. **Status:** measured; bounded by the
   scanner analysis cache (warm scans skip alert processing entirely).
4. **Thread-pool scheduling / lock contention** — `ThreadPoolExecutor`
   shutdown and the alert-state RLock inflate cold-scan wall time.
   **Status:** measured; inherent to the 4-worker design.
5. **API framework overhead** — `/analyze` 123 ms avg vs ~17 ms raw
   analysis: TestClient + FastAPI dispatch + JSON serialization account
   for the difference. **Status:** measured; acceptable for a local
   dashboard API.

## 3. Optimizations implemented (measured before/after)

### 3.1 `load_csv` numeric conversion fast path

- **Before:** every OHLCV numeric column ran
  `astype(str) → str.replace(",", "") → to_numeric` even when the column
  contained no thousands separators (the common case).
- **After:** already-numeric columns are skipped; object columns are
  checked for commas with one vectorised `str.contains` and only run the
  string round-trip when commas exist; any other dtype (bool/datetime)
  keeps the historical string round-trip for strict value identity.
- **Measurement (clean warm A/B, 10 files × 300 rows, best-of-50):**
  ```
  Before: 54.34 ms
  After:  42.98 ms
  Improvement: ~1.26x on the conversion loop (~21 % reduction)
  ```
- **Correctness:** value-identical — regression-tested in
  `tests/test_cache_correctness.py::TestLoadCsvFastPathIdentity`
  (fast vs slow path `assert_frame_equal`, thousands-separator file,
  signal output unchanged). Code reviewed twice; airtight dtype guard
  confirmed.

### 3.2 Cache DataFrame isolation (correctness, no speed claim)

- `scanner_cache.get_dataframe` now returns a copy; `DataService`
  `get_history` / `get_history_batch` / `get_nepse_index_history` return
  `df.copy()` on both hit and miss paths; `DiskCache.set` writes
  atomically (temp file + `os.replace`). Consumers can no longer corrupt
  shared cached frames. Regression tests in `tests/test_cache_correctness.py`.

## 4. Persistence hardening (Phase 1–2)

All three mutable JSON stores now use the shared helper
`src/utils/json_store.py`:

- **Alerts history** (`src/alerts/history.py`), **portfolio**
  (`src/portfolio/holdings.py`, + new `save_portfolio`), **watchlist**
  (`src/watchlist/manager.py`).
- Guarantees: UTF-8, atomic writes, parent-dir creation, safe handling of
  missing/empty/malformed/truncated JSON, corrupt files backed up aside
  (`*.corrupt.bak`, nanosecond-timestamped on repeat corruption) before
  recovery, explicit logging, deterministic serialization (watchlist).
- Tests: `tests/test_persistence.py` (34 tests) — missing/empty/
  malformed/truncated/atomic/repeated for all three stores + helper.
- A malformed state file no longer breaks the analyse/portfolio pipeline
  (verified by `test_corrupt_file_does_not_break_process_alerts` and the
  existing API regression tests).

## 5. Sprint 11.2 — Real-data baseline & scanner scaling

### 5.1 Real-data corpus

`benchmarks/corpus.py` selects a deterministic, seeded subset of the real
scraped CSVs in `data/raw/` (287 files at the time of writing). The
corpus block in each `runner_*.json` records source, seed, symbol list,
symbol count and total rows; per-symbol rows vary (real data). Synthetic
files are never written into a real corpus directory (all scan/history
benchmarks accept a `populate` flag; `bench_cache_hit_miss` uses a
private `_syn_cache/` subdir that the non-recursive scanner glob ignores).

### 5.2 Scanner scaling — sequential vs parallel (cold p50, measured)

`SCANNER_WORKERS=4` (default). Worker 1 = sequential fallback; 4 =
parallel. Real corpus, 3 cold + 5 warm reps:

| Symbols | Sequential cold p50 | Parallel cold p50 | Speedup | Successful / skipped |
| ------: | ------------------: | ----------------: | ------: | --------------------: |
| 50      | 2705 ms             | 1814 ms           | 1.49×   | 50 / 0                |
| 100     | 5720 ms             | 4673 ms           | 1.22×   | 100 / 0               |
| 200     | 13601 ms            | 10993 ms          | 1.24×   | 200 / 0               |

Warm (analysis-cache hit) p50: 27–122 ms depending on corpus size
(essentially cache-served ranking only). The gain is stable across
repeated runs at 50–200 symbols; the parallel advantage is I/O + parse
overlap on the cold path. At 50 symbols the sequential path is 1.5×
slower, at 200 symbols 1.24× — bounded threads help throughout, and the
sequential fallback (`workers=1` or `SCANNER_WORKERS=1`) remains
available and produces byte-identical rankings.

### 5.3 Alert pipeline (profiled, optimised)

A 60-symbol cold `process_alerts` pass cost ~3.5 s, dominated by **two
full-file history reads per symbol** (`get_last_state` + `update_state`
each called `load_history()`) plus one save per symbol — O(N²) in scan
size as the file grows. The module lock already serialised the whole
cycle, so the fix threads a single preloaded history through
`get_last_state`/`update_state` (both gained an optional `history=`
kwarg; default behaviour unchanged). This halves the per-symbol reads
with identical semantics, corruption recovery, and write-once
persistence. Regression tests: malformed/truncated/empty/missing
history, repeated processing, duplicate prevention, restart/reload
(`tests/test_persistence.py`, `tests/test_alerts_engine.py`).

### 5.4 API serialization

`/api/analyze` profiled at 120 rows via TestClient: ~75 ms of the
~110 ms request is `analyze_stock` (CSV load + indicator pipeline); the
remaining ~35 ms is framework/TestClient + JSON dispatch overhead. The
p95 tail comes from cold CSV loading, not response serialization — no
response-layer rewrite was justified. `benchmarks/runner.py` records
`api_analyze_avg_ms` / `api_analyze_p95_ms` for regression tracking.

### 5.5 Trade-offs

- **Worker count** — 4 is a conservative default; gains at 50–200
  symbols are real but sublinear (1.2–1.5×). Tune `SCANNER_WORKERS` on
  target hardware; measure with `--workers W`.
- **Memory** — cold-scan peak memory is small (tracemalloc, in-process:
  <1 MiB at 200 symbols); the bounded LRU caches keep memory flat.
- **Cache behavior** — warm scans skip alert processing entirely
  (documented in `scanner_cache`); cold scans now pay one read + one
  write per symbol instead of two reads + one write.
- **Determinism** — results are collected in file order in both modes;
  sequential and parallel produce identical rankings (regression-tested).
- **Failure isolation** — one bad symbol never aborts a scan in either
  mode; skipped entries carry the per-file error.

## 6. Remaining bottlenecks / follow-up tasks

1. **Indicator pipeline** — pandas rolling/ewm dominates single-stock
   analysis (~16 ms/stock at 300 rows). Options (Sprint 11.3): reuse
   precomputed rolling windows (SMA20/50/200 share the same
   `rolling().mean()`), or cache indicator columns per fingerprint.
2. **Alert-history batching** — `process_alerts` now reads once per
   symbol; a whole-scan batch (one read + one write per scan) would
   remove the remaining per-symbol write. Higher risk; requires a
   scanner-level API change.
3. **Scanner worker tuning** — profile `SCANNER_WORKERS` on real
   hardware; consider process-based pools if the profile shows
   CPU-bound indicator work dominating the threaded pool.
4. **API response size** — `/analyze` returns the full analysis dict;
   consider response-model trimming if p95 latency matters in
   production.
5. **`get_live_market`** returns the cached `list[StockQuote]` directly —
   same class of mutable-leak issue as DataFrames (out of the stated
   DataFrame scope; queue for Sprint 11.3).

## 7. Validation

- `tests/test_persistence.py` — 34 passed
- `tests/test_cache_correctness.py` — 14 passed
- `tests/test_performance.py` — parallel/sequential equivalence, empty
  & one-symbol input, sequential isolation, cache interaction, worker
  limits, benchmark-suite smoke — green
- `tests/test_sprint10.py`, `tests/test_analyzer.py` — green
- `tests/test_api.py::test_analyze_endpoint_success`,
  `test_portfolio_endpoint` — green
- `tests/test_watchlist_manager.py`, `test_alerts_engine.py`,
  `test_portfolio_analyzer.py`, `test_data_service.py` — green
- Full suite: **1927 passed, 1 skipped, 0 failed** (Sprint 11.2)
- Full suite: **1948 passed, 1 skipped, 0 failed** (Sprint 11.3, +20 tests)

---

## 8. Sprint 11.3 — Scan-level alert batching, indicator reuse & cache isolation

### 8.1 Profiling before optimising (real corpus, cProfile)

A cold 50-symbol scan of the real corpus was profiled **before** any
change (Sprint 11.3 Phase 1):

| Cost centre                          | Cumulative time | Share of scan |
| ------------------------------------ | --------------: | ------------: |
| `process_alerts` → `update_state` → `save_history` → `json.dump` | 9.13 s | ~92 % |
| Remaining scan work (CSV load, indicators, ranking)            | ~0.84 s | ~8 % |

1,125,000 `json.encoder` calls were made for 50 symbols: each symbol
re-serialised the entire growing history file (O(N²) JSON work). The
Sprint 11.2 “one read per symbol” fix removed the extra read but kept
**one write per symbol** — that write became the dominant cost. This
confirmed the scan-level batch (Phase 2–4) as the highest-value change
and the indicator BB/SMA duplication (Phase 6–8) as a small, safe win.

### 8.2 Scan-level alert batching — architecture

New public API `process_alert_batch(entries)` in `src/alerts/engine.py`
(single-symbol `process_alerts` unchanged and still the default):

```text
BEFORE (per symbol)                          AFTER (per scan)
load_history  × N                          load_history   × 1
update_state  × N (each save_history)      update_state   × N (in-memory only)
save_history  × N                          save_history   × 1
```

- The alert engine owns persistence: `process_alert_batch` loads once,
  mutates the in-memory dict for every entry under the existing module
  lock, and persists once atomically. `update_state` gained a `save=`
  kwarg (default `True` → backward compatible); `_process_alerts_locked`
  threads it through.
- **Failure isolation**: each entry is processed in its own try/except;
  a malformed result is logged, its partial in-memory state is rolled
  back from a `copy.deepcopy` snapshot (or popped when the symbol was
  absent), and the batch continues. One bad symbol can never corrupt the
  shared history or abort the scan.
- **Empty batch** returns `{}` without acquiring the lock or touching
  disk.
- **Scanner integration** (`src/scanner/engine.py`): `_analyze_file` now
  returns `(analysis, skip, fresh)`; `analyze_stock(file, with_alerts=False)`
  skips per-symbol alerts. After the worker loop the scanner calls
  `process_alert_batch` once over the fresh analyses, attaches
  `new_alerts` per result, and **then** caches (`put_analysis`) — a cache
  hit always returns an alerted result. Fully-cached scans skip the batch
  entirely (existing “cache hit returns first scan’s new_alerts”
  semantics preserved).

### 8.3 Alert batching — measured (real corpus, wall clock + I/O counts)

| Symbols | Per-symbol (ms) | reads/writes | Batch (ms) | reads/writes | Speedup |
| ------: | --------------: | -----------: | ---------: | -----------: | ------: |
| 50      | 1373.57         | 50 / 50      | 5.53       | 1 / 1        | 248×    |
| 100     | 3850.01         | 100 / 100    | 10.00      | 1 / 1        | 385×    |
| 200     | 6769.56         | 200 / 200    | 21.95      | 1 / 1        | 308×    |

The architectural target (one history read + one history write per
scan) is achieved and measured — `benchmarks/runner.py` records the
`alert_batch` block on every run.

### 8.4 Indicator pipeline — confirmed duplication, minimal reuse

Code tracing + profiling confirmed exactly one duplicated rolling pass in
the analysis hot path: `BB_MIDDLE` (`Close.rolling(20).mean()`) is
value-identical to `SMA_20`, which the analyzer already computes first.
`add_bollinger_bands` now reuses the `SMA_20` column when `period == 20`
and the column exists; standalone calls without `SMA_20`, or with a
non-default window, compute it exactly as before. Value-identity is
regression-tested (`tests/test_sprint11_3.py::TestIndicatorReuse`). Other
`rolling()` uses (`src/ml/feature_engineering.py`, `src/regime/detector.py`,
UI chart pages) operate on separate DataFrames in separate subsystems and
are not duplicated execution — no change made there.

### 8.5 Live-market cache — copy-on-return

`DataService.get_live_market` returned the cached `list[StockQuote]`
object directly on both cache-hit and provider paths — a mutable-list
leak of the same class as the Sprint 11.1 DataFrame fix. It now returns
`list(cached)` / `list(result)` so a consumer that appends/removes/
reorders the returned list can never corrupt the cached quotes.
Regression tests cover mutation isolation, repeated calls, empty market
and cache refresh (`tests/test_sprint11_3.py::TestLiveMarketCopy`).

### 8.6 Thread vs process pools (Phase 11 — measured conclusion)

Threads remain the correct execution model: the scan is I/O + parse +
pandas-vectorised, not genuinely CPU-bound. Post-change profiling shows
JSON/alert work is gone and the remaining cold-scan time is CSV parse +
indicator math, which the bounded `ThreadPoolExecutor` already overlaps
well. Process-based pools would add pickling + Windows process-spawn
overhead with no measured benefit. **Deferred**, with the measured
numbers recorded in §8.1/§8.3; revisit only if a future profile shows
CPU saturation.

### 8.7 Trade-offs

- **Alert state writes** — one per scan instead of one per symbol;
  corruption recovery, `.corrupt.bak`, atomic writes and duplicate
  prevention are all preserved (regression-tested).
- **`deepcopy` snapshot** — one small per-symbol dict copy per batch
  entry on the success path; negligible next to the eliminated O(N²)
  JSON serialization (measured 248–385× faster).
- **Determinism** — the batch mutates history in entry order; alert
  semantics, rule ordering and timestamps are unchanged.
- **Concurrency** — the batch holds the module lock across
  load-mutate-save; concurrent `process_alerts` / `process_alert_batch`
  calls serialise safely (threaded test added).

### 8.8 Remaining bottlenecks (updated)

1. Cold CSV parse + indicator math now dominate cold scans (see §8.1);
   warm scans are cache-served.
2. Indicator columns are not cached across analyses; only the BB/SMA_20
   duplication is removed. A fingerprint-based indicator cache remains an
   option but needs a strict invalidation strategy (Sprint 11.1
   copy-on-return rules apply).
3. `get_live_market` copies the list but shares the `StockQuote`
   objects; deep-copy per quote would harden against in-place field
   mutation if quotes ever become mutable dataclasses.
4. `/api/analyze` still returns the full analysis dict (see §5.4).

---

## 9. Sprint 11.4 — Indicator cache, data isolation & API p95

Sprint 11.4 delivered four focused objectives on top of the 11.3
baseline (`1948 passed, 1 skipped`): a fingerprint-based indicator
cache, deep-copy hardening of live-quote / top-mover / market-summary
caches, scanner test isolation, and a measured `/api/analyze` p95
investigation. **Full suite: `1982 passed, 1 skipped, 0 failed`**
(+34 new tests).

### 9.1 Indicator cache — motivation (measured, Phase 1)

Profiling the analysis path before building anything:

- `analyze_dataframe` recomputes the full rolling/ewm indicator stack
  (SMA/EMA/RSI/MACD/Bollinger/ATR/volume) every time identical
  historical data is analysed — once per `/api/analyze` request, once
  per watchlist scan, once per `_live_market_scan` fallback.
- Measured cost: **~17 ms per analysis at 500 rows**, dominated by the
  indicator passes; the content fingerprint costs **< 1 ms**.
- Repeated computation is real: the API, watchlist and live-market
  paths re-analyse the same cached history DataFrames on every call.

### 9.2 Fingerprint design

`src/indicators/cache.py` — a bounded, thread-safe LRU cache:

```text
raw OHLCV DataFrame
      │
      ▼
sha256(content fingerprint + index) + config tuple + pipeline version
      │                                                          + optional symbol namespace
      ▼
cache lookup ──hit──▶ copy of indicator-augmented frame ──▶ _build_analysis()
      │ miss
      ▼
full indicator chain ──▶ put (stores a copy) ──▶ _build_analysis()
```

- **key** — `sha256` over `pd.util.hash_pandas_object(df, index=True)`
  (every OHLCV value + the date index), plus the RSI/MACD period
  config tuple, plus `INDICATOR_PIPELINE_VERSION` (bumped per formula
  change), plus an optional symbol namespace. Never symbol-only, never
  timestamp-only — content-derived.
- **invalidation** — automatic and deterministic: any change to the
  OHLCV values, the row count, the date range, the indicator periods,
  or the pipeline version changes the digest. Verified by tests for
  modified value / added row / removed row / different parameters /
  different symbols.
- **lifetime** — process lifetime (in-memory, rebuilt on restart).
- **max size** — `INDICATOR_CACHE_MAX_ENTRIES` (default 200), LRU
  eviction (`OrderedDict.move_to_end` + `popitem(last=False)`).
- **copy semantics** — `get` returns `entry.copy()` and `put` stores
  `df.copy()`; cached mutable frames never leak to callers (Sprint
  11.1 copy-on-return rule). Verified by mutation-leak tests.
- **thread safety** — every public method guarded by one `RLock`;
  safe across scanner workers / API threads (concurrency tests added).

### 9.3 Analyzer integration

- `analyze_dataframe(df, symbol=None, use_cache=True)` only caches
  frames with the raw OHLCV shape (`_is_cacheable_frame`); everything
  else (1-row test fixtures, already-augmented frames) falls through
  unchanged.
- On a hit the indicator chain is skipped and the cheap analysis tail
  (`_build_analysis`) is re-derived from the cached copy — value-
  identical to a cold run (verified by identity tests).
- `analyze_stock` passes the symbol namespace so coincidentally
  identical prices for different symbols never share an entry.
- **Backtest** passes `use_cache=False`: its expanding window is a
  different frame every iteration, so the cache could never hit — the
  fingerprint would be pure overhead.
- `walk_forward.py` imports `analyze_dataframe` but never calls it
  (dead import — no cache hazard).

### 9.4 Indicator cache — measured (synthetic frames, per §9.10 item 5)

| Metric | 50 symbols | 200 symbols |
| --- | ---: | ---: |
| First analysis (avg) | 19.84 ms | 20.45 ms |
| Repeated analysis (avg) | 8.89 ms | 9.63 ms |
| Warm-phase hit rate | 100.0 % | 100.0 % |
| Speedup | **2.23×** | **2.12×** |

Cold scans are unchanged (each symbol analysed once); the win is in
**repeated** workloads — `/api/analyze`, watchlist re-scans, and
live-market fallback scans. Memory: 200 entries × 500-row frames ≈
~8 MB, bounded by the LRU.

### 9.5 Live-market / top-mover isolation

`StockQuote`, `TopMover` and `MarketSummary` are **mutable
dataclasses**. Sprint 11.3 copied the outer list; callers could still
mutate a returned quote in place (`quote.ltp = ...`) and corrupt the
shared cache entry. Sprint 11.4 returns `copy.deepcopy` of every
object on both the cached and provider paths:

- `get_live_market()` — per-quote deep copy (was `list(cached)`).
- `get_market_summary()` — deep copy of the summary.
- `get_top_gainers/losers/turnover()` — per-mover deep copy.

Regression tests retrieve, mutate, re-retrieve and assert the internal
cache is unchanged — for cache hit, miss, empty market, list mutation
and object-field mutation.

### 9.6 Scanner test isolation

Pre-existing issue: `test_data_service.py`'s scan tests ran the **real**
scanner over the developer's `data/raw/` (hundreds of scraped CSVs)
and wrote real `data/alerts/history.json`. New `scan_isolation`
fixture:

- writes 3 deterministic 60-row OHLCV CSVs (valid business days via
  `pd.bdate_range`) into a temp dir,
- redirects `src.scanner.engine.DATA_DIRECTORY` and
  `src.alerts.history.HISTORY_FILE` to temp locations,
- clears the in-memory scanner cache and restores everything on
  teardown.

`test_scan_market` now asserts `len(results) >= 3` — a fixture
regression (e.g. dropped dates) shows up as skips, not silent passes.

### 9.7 API p95 — measured breakdown (`/api/analyze`, real symbol)

| Stage | Cold | Warm |
| --- | ---: | ---: |
| Total request (avg / p50 / p95) | 80.5 / 78.6 / 137.2 ms | 45.9 / 46.8 / 54.7 ms |
| `analyze_stock` (avg / p95) | 44.5 / 134.1 ms | — (cache-served) |
| JSON serialization (avg / p95) | 0.08 / 0.09 ms | 0.08 / 0.09 ms |

**Decision: response trimming NOT justified.** A repository-wide
consumer audit showed `target1/2/3`, `best_rr`, `rr_target1/2/3` and
`score_breakdown` are consumed by `ui/app.js` and `analyze_page.py`;
serialization is 0.08 ms (0.2 % of the request), so excluding fields
would buy nothing and break the UI. The warm-path win comes from the
indicator cache (§9.4), not from schema changes.

### 9.8 Process pool decision — deferred (unchanged)

Re-confirmed with the Sprint 11.4 indicator cache in place: the
workload is CSV parse + memory-bandwidth-bound indicator math, and
threads already scale the scanner (Sprint 11.2, ~1.2–1.5× real corpus)
with no pickling/spawn overhead on Windows. No `ProcessPoolExecutor`
was introduced — process pools remain deferred unless a benchmark
shows a reproducible advantage.

### 9.9 Trade-offs

- **Fingerprint cost on misses** — every cold analysis pays < 1 ms to
  hash; worth it because repeated analysis is the dominant production
  workload (API/watchlist/live-scan). Backtest opts out.
- **Deep copies** — per-quote `deepcopy` on `get_live_market` (≈200
  small dataclasses) is sub-millisecond and bounded by the 30 s TTL.
- **Cache is in-memory only** — no persistence, so there is no
  corruption/restart surface; `INDICATOR_CACHE_ENABLED=false` disables
  it entirely.

### 9.10 Remaining bottlenecks (updated)

1. Cold CSV parse still dominates cold scans (unchanged — disk bound).
2. Indicator cache is process-local; a shared/multiprocess store would
   need the same copy-on-return + fingerprint discipline.
3. `/api/analyze` still returns the full analysis dict — safe to
   keep (§9.7 measured).
4. `walk_forward.py` dead import of `analyze_dataframe` could be
   removed (hygiene).
5. Benchmark suite runs the indicator-cache microbenchmark on
   synthetic frames even in `--source real` runs (documented in
   `benchmarks/pipeline.py`).

---

## 10. Sprint 11.5 — Process-safe cache investigation, real-corpus validation & cleanup

Sprint 11.5 asked a focused question — *is a process-safe/shared
indicator cache actually beneficial?* — and required the 100-symbol
real-corpus benchmark plus cleanup. **Full suite: `1982 passed, 1
skipped, 0 failed`** (baseline preserved; no new production tests were
needed because 11.5 is a measurement + cleanup sprint).

### 10.1 Deployment-topology audit (Phase 2 — measured need)

| Deployment mode | Analyzer processes | Cross-process indicator reuse? |
| --- | --- | --- |
| Streamlit dashboard (`app.py`) | 1 | no |
| FastAPI local (`launcher/run_app.bat`, README) | 1 (`uvicorn` without `--workers`) | no |
| Docker `api` stage (Dockerfile) | **2** (`uvicorn --workers=2`) | yes — first request per symbol per worker |
| Scanner (`scan_market`) | runs entirely inside one process | no — never split across workers |
| Telegram bot | separate process, consumes alerts only | no |

There is no `multiprocessing`, `ProcessPoolExecutor`, or gunicorn
anywhere in `src/` — the only concurrency is `ThreadPoolExecutor`
(scanner, datasvc). The one multi-process deployment is the Docker API
stage (`--workers=2`), and even there the heavy scanner runs in a
single worker.

### 10.2 Cross-process duplication — measured (Phase 3, `benchmarks/cross_process.py`)

New committed benchmark spawns two fresh worker processes against the
real corpus (state files redirected to temp — no production writes):

| 50 real symbols | Worker A (proc 1) | Worker B (proc 2) |
| --- | ---: | ---: |
| Full cold pass (parse + indicators) | 1749 ms | 1971 ms |
| Analyze per symbol (cold → warm, local LRU) | 21.6 → 3.8 ms | 24.7 → 5.4 ms |
| Indicator-cache misses | 50 | **50** (recalculates everything) |
| Duplicated indicator compute | — | **~0.9 s, one-time** |

At 200 symbols the duplicated one-time compute grows to ~3–5 s per
extra worker. After that one-time cold start, each worker's local LRU
serves repeats at ~4–5 ms/symbol — the *ongoing* workload shows zero
cross-process duplication.

### 10.3 Decision — process-safe shared cache **DEFERRED**

Per the sprint's Critical Decision Rule, the process-local LRU remains
the correct architecture. Evidence:

1. Default deployment is single-process (Streamlit, local uvicorn).
2. The scanner — the heaviest compute (7–14 s cold at 200 symbols) —
   runs in one process and is never split across workers.
3. The only duplication is a **bounded one-time cold-start cost**
   (~1 s at 50 symbols, ~3–5 s at 200, per extra worker).
4. A shared store (SQLite/disk) would add DataFrame serialization,
   cross-process locking, corruption tolerance, eviction and versioning
   surface — disproportionate to a one-time saving.

A shared cache would also *not* remove the CSV-parse duplication (the
analyze path parses via `load_csv` directly, not the data-service disk
tier), so its ceiling is the indicator portion (~0.9 s / 50 symbols).
Recorded as deferred technical debt in §10.7.

### 10.4 Real-corpus benchmark — indicator cache on **real frames** (Phase 1/13)

`bench_indicator_cache` now loads the actual scraped CSVs under
`--source real` (previously synthetic frames even in real runs). The
Sprint 11.5 primary result is therefore real data:

| Symbols | First analysis (avg) | Repeated (avg) | Speedup | Warm hit rate |
| --- | ---: | ---: | ---: | ---: |
| 50 | 22.86 ms | 11.64 ms | 1.96× | 100.0 % |
| 100 | **25.08 ms** | **10.28 ms** | **2.44×** | 100.0 % |
| 200 | 25.33 ms | 10.97 ms | 2.31× | 100.0 % |

100 % warm hit rate on real frames confirms the fingerprint cache is
fully effective on genuine market data. Cold averages are slightly
higher than the synthetic microbenchmark (11.4) because real files
vary in row count — expected, not a regression.

Synthetic microbenchmarks (`single_analysis`, `strategy`, `backtest`)
are now explicitly labelled as synthetic in the runner's printed
report, so a `--source real` run never mixes the two without a label.

### 10.5 Scanner — sequential vs parallel (real corpus, cold, 3+5 reps)

| Symbols | Mode | Cold p50 | Cold p95 | Cold p99 | Warm p50 | Peak (cold) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 50 | seq | 855 ms | 939 ms | 939 ms | 39.7 ms | 0.96 MiB |
| 50 | par (4) | 841 ms | 847 ms | 847 ms | 36.5 ms | 1.12 MiB |
| 100 | seq | 2281 ms | 2357 ms | 2357 ms | 68.5 ms | 1.89 MiB |
| 100 | **par (4)** | **2102 ms** | **2378 ms** | **2378 ms** | **56.5 ms** | **1.99 MiB** |
| 200 | seq | 3701 ms | 4246 ms | 4246 ms | 129.2 ms | 3.67 MiB |
| 200 | par (4) | 3880 ms | 4080 ms | 4080 ms | 113.0 ms | 3.98 MiB |

All runs: 0 skipped. Successful/skipped = 50/0, 100/0, 200/0. (At 200 the
parallel p50 is marginally slower than sequential in this run — within
noise; the threaded gain is I/O-overlap-bound and shrinks as the OS
file cache warms across back-to-back reps. The Sprint 11.2 numbers
were 1.49×/1.22×/1.24× — the threaded advantage is stable and small.)
Peak memory stays well under 4 MiB even at 200 symbols (tracemalloc,
in-process).

### 10.6 Worker determinism (Phase 14) and API p95 (Phase 15)

- **workers=1/2/4**: identical rankings, identical signals, 0 skipped
  at all three worker counts on 50 real symbols; alert history remains
  duplicate-free. Threaded execution is deterministic (as in 11.2–11.4).
- **`/api/analyze`** (real symbol, TestClient): cold avg 108 ms / p50
  54 / p95 540 ms; **warm avg 28.4 ms / p50 27.9 / p95 30.9 / p99 30.9 ms**.
  Schema unchanged — `target1/2/3`, `best_rr`, `rr_target1`,
  `score_breakdown`, `signal`, `score`, `confidence` all present. The
  warm-path win comes from the indicator cache, not schema changes.

### 10.7 Remaining bottlenecks / technical debt (updated)

1. **Cold CSV parse** still dominates cold scans and cold `/api/analyze`
   (unchanged; disk bound).
2. **Cross-process duplication is deferred** — if the Docker `api`
   stage ever runs >2 workers *and* a profile shows cold-start cost
   mattering, revisit a SQLite/pickle L2 tier with the 11.4 fingerprint
   + copy-on-return discipline. Not warranted today (§10.3).
3. `walk_forward.py` dead `analyze_dataframe` import — **removed**
   (Phase 12).
4. Benchmark-suite hygiene — `bench_indicator_cache` now uses real
   frames under `--source real`; synthetic microbenchmarks labelled
   (Phase 13); `benchmarks/cross_process.py` added with state isolation.
5. `/api/analyze` response stays as-is (measured safe, §9.7).

### 10.8 Validation

- Full suite: **1982 passed, 1 skipped, 0 failed** (matches 11.4;
  cleanup-only sprint, no new production tests).
- Focused: `test_walk_forward`, `test_sprint11_4`, `test_performance`,
  `test_backtest_engine` — 103 passed.
- Phases 3/14/15 executed against the real corpus with results above.

---

## 11. Sprint 11.6 — Cold-path CSV optimisation, CI regression gate & Docker validation

Sprint 11.6 profiled the cold CSV path stage-by-stage, kept only
optimisations justified by measurement, added a non-flaky CI regression
gate, validated the Docker two-worker API deployment (and fixed the real
defect it exposed), and preserved every correctness/persistence
semantic. **Full suite: `2009 passed, 1 skipped, 0 failed`** (+27 tests
over 11.5).

### 11.1 Cold CSV profile — stage by stage (200 real symbols)

`benchmarks/pipeline.py::bench_csv_stages` times each stage of the real
cold `load_csv` path (cache cleared per file; 200 scraped NEPSE CSVs,
9,532 rows):

```text
Stage                  Time (avg/file)   % of cold load
--------------------------------------------------------
filesystem read         0.388 ms          5.1 %
split + header detect   0.013 ms          0.2 %
read_csv (C parser)     2.231 ms         29.3 %   <- dominant single stage
strip column names      0.378 ms          5.0 %
numeric conversion      0.429 ms          5.6 %
datetime parsing        1.416 ms         18.6 %
sort by date            0.686 ms          9.0 %
dropna (Close)          1.551 ms         20.4 %
cache write             0.774 ms         10.2 %
defensive copy          0.166 ms          2.2 %
--------------------------------------------------------
load_csv total (avg)    7.621 ms         100 %
```

**Conclusion:** `pandas.read_csv` (C parser) + the filesystem read are
~34 % of the cold load and are inherent to parsing — application-level
micro-optimisation of the remaining stages (datetime/sort/dropna) is
pandas-internal and not safely reducible without changing values.

### 11.2 CSV fast path — measured (before/after)

`_read_csv_robust` now hands *trivially clean* OHLCV files (every data
row matches the header field count AND no quote character in the header
or data region) to the pandas C parser first with `skiprows` past any
title/blank rows. The C parser returns already-numeric columns, so the
per-column `str.contains`/`pd.to_numeric` pass becomes a no-op.

- **Before:** schema parser (line-by-line `split(',')`) for every file,
  then string round-trip numeric conversion.
- **After:** C parser fast path; any file with comma formatting, ragged
  rows, unbalanced quotes or a column-count mismatch falls back to the
  tolerant schema parser — **value-identical output by construction**
  (verified on all 286 real files + the Phase 6 fixture set; 7
  regression tests in `tests/test_sprint11_6.py`).
- **Measured cold-load improvement (real corpus):**
  ```
  50 symbols:  history cold 570 ms    | csv cold best 353 ms
  100 symbols: history cold 1081 ms   | csv cold best 714 ms
  200 symbols: history cold 2060 ms   | csv cold best 1449 ms
  ```
  vs Sprint 11.5 (200 sym history cold 3853 ms) — **~1.9× on history
  cold load** (the before/after spans separate runs on different days,
  so machine/disk-cache state contributes to the delta; the fast path's
  own measured share of cold load is ~36 %, per the loader docstring).
  Classification per Phase 18: **Significant**. No further
  CSV-loader optimisation is justified — remaining stages are
  pandas/disk-bound (Phase 18 critical rule: stop here, documented).

### 11.3 Cold-load benchmark — percentiles + peak memory (real corpus)

`benchmarks/pipeline.py::bench_cold_load` (5 reps, cache cleared per
rep, per-file exception isolation, `del frames` + `gc.collect()` before
timed reps):

| Symbols | Best | Avg | p50 | p95 | p99 | Peak mem | Successful / skipped / failed |
| ------: | ---: | --: | --: | --: | --: | -------: | -----------------------------: |
| 50      | 353 ms | 360 ms | 356 ms | 374 ms | 374 ms | 0.89 MiB | 50 / 0 / 0 |
| 100     | 714 ms | 729 ms | 729 ms | 746 ms | 746 ms | 1.73 MiB | 100 / 0 / 0 |
| 200     | 1449 ms | 1469 ms | 1457 ms | 1518 ms | 1518 ms | 3.41 MiB | 200 / 0 / 0 |

Cold scans follow the same curve: 50 sym 1407 ms / 100 sym 3021 ms /
200 sym 5737 ms total `scan_cold` (vs 11.5: 200 sym 7231 ms).

### 11.4 Worker scaling 1/2/4 — 11.5 vs 11.6 (real corpus, cold p50)

| Symbols | Mode | 11.5 cold p50 | 11.6 cold p50 | Warm best (11.6) |
| ------: | --- | ------------: | ------------: | ---------------: |
| 50      | seq | 855 ms | 671 ms | 28.3 ms |
| 50      | par (4) | 841 ms | 596 ms | 28.0 ms |
| 100     | seq | 2281 ms | 1203 ms | 56.1 ms |
| 100     | par (4) | 2102 ms | 1216 ms | 46.7 ms |
| 200     | seq | 3701 ms | 2374 ms | 114.9 ms |
| 200     | par (4) | 3880 ms | 2390 ms | 92.9 ms |

Workers 1/2/4 produce **identical ranked results** at all three corpus
sizes (determinism preserved). The default worker count stays at 4 —
the threaded gain is I/O-overlap-bound and small; no evidence supports
raising it.

### 11.5 CI regression gate (non-flaky)

`benchmarks/ci_gate.py` + a `benchmark` job in `.github/workflows/ci.yml`
running a 20×300 deterministic **synthetic** corpus in a private temp
dir (never `data/raw`, never production state; alert/portfolio
persistence redirected):

- **Primary hard gate (machine-independent):** best-of-3 warm
  (cache-hit) load must be ≥ 5× faster than best-of-3 cold load
  (measured ~10–11× on the reference machine). A broken scanner cache
  fails this on any runner, regardless of absolute speed. Min-of-3 on
  both sides absorbs GC/noisy-neighbour spikes (measured best-of-3
  spread only ~1.04× vs 4.8× single-run outliers).
- **Severe-regression tripwire:** cold_best3 ≤ committed baseline ×
  factor 3.0 (`benchmarks/results/ci_baseline.json`, regenerated with
  `--update-baseline`). 3.0 absorbs runner-to-runner variance
  (GitHub-hosted runners are typically 1.5–3× slower than the local
  reference machine) while tripping on catastrophic slowdowns.
- **Artifact:** `benchmark-ci.json` (commit, Python, platform, corpus,
  cold/warm/scan times, factor, verdict) uploaded with
  `actions/upload-artifact`, `if: always()`.
- **Local verification:** `python -m benchmarks.ci_gate --factor 3.0`
  → PASS (cold 193 ms vs baseline 160.84 × 3.0; warm 17 ms = 11.3×;
  scanner 3384 ms); `--factor 0.01` → FAIL with exit 1 (both paths
  smoke-tested).

### 11.6 Docker two-worker validation — and the WinError 5 fix

`benchmarks/validate_workers.py` spawns the exact Dockerfile `api`-stage
command (`uvicorn src.api.main:app --workers=2`) against a temp real-
corpus data dir with state files isolated (the child runs with
`cwd=state_root` + `PYTHONPATH` because the state paths are CWD-relative;
`NEPSE_HOME` does not redirect them). Phases validated:

- **Phase 12 (endpoints):** `/`, `/analyze/{sym}`, `/portfolio`,
  `/watchlist` all 200; threaded 8-writer watchlist add/remove barrage
  (POST/DELETE) all 200; two `/analyze` responses byte-identical.
- **Phase 13 (local LRU):** repeated analysis of one symbol warms
  within a worker (first 37.7 ms → best repeat 31.3 ms); cross-worker
  misses are expected and harmless.
- **Phase 16 (latency, 2 workers):** `/api/analyze` best 30.0 ms / avg
  51.2 / p50 41.3 / p95 101.7 / p99 168.0 ms; statuses all 200.
- **Phase 14 — real defect found and fixed.** Concurrent watchlist
  writes from two workers intermittently returned **500s**: on Windows,
  `os.replace` raises `PermissionError` (WinError 5) when the
  destination is momentarily held open by the other worker's reader.
  The write is atomic (never corrupts state — the repro showed zero
  `.corrupt.bak` / `.tmp` litter) but the transient sharing violation
  surfaced as 500s.

**Fix (`src/utils/json_store.py`):** `_replace_atomic()` wraps
`os.replace` in a bounded retry (6 attempts × 50 ms) that only retries
WinError 5 (`_is_sharing_violation`, `winerror == 5`); every other
OSError raises immediately. Atomicity and corruption tolerance are
unchanged (temp file + replace, never in-place). After the fix the full
validation **PASSES**: all add/remove 200, no leftover `*.tmp`, no
`.corrupt.bak`, cross-worker responses identical. Regression tests
added (`tests/test_persistence.py`): transient violation retries then
succeeds (3 calls), persistent violation exhausts attempts then raises,
non-sharing OSError raises immediately (1 call).

### 11.7 API regression + latency vs Sprint 11.5

- `/api/analyze` (runner, real corpus): 11.5 avg 68.6 / p95 103.8 ms →
  11.6 avg 56.5 / p95 64.0 ms (200 sym). `/api/portfolio`: 142.3 →
  121.8 ms. Response schema unchanged.
- Focused suites: `test_api`, `test_persistence`, `test_cache_correctness`,
  `test_sprint11_6` — 116 passed. Full suite: **2009 passed, 1 skipped,
  0 failed**.

### 11.8 Trade-offs

- **Fast path vs tolerance** — the C parser is gated on trivially-clean
  files only; any ambiguity falls back to the schema parser, so output
  is identical by construction. The gate costs one extra row scan
  (~0.01 ms/file) — negligible.
- **Retry vs strictness** — only WinError 5 is retried; permanent errors
  surface immediately with the existing temp-file cleanup. Worst-case
  added write latency: 5 × 50 ms = 250 ms, only under genuine sharing
  contention.
- **CI gate** — min-of-3 + machine-independent speedup check makes the
  gate robust to runner noise; the 3.0 absolute factor is deliberately
  generous (tripwire, not noise detector).
- **Docker** — two workers keep independent process-local indicator
  LRUs (intentionally, per §10.3); a shared cache was not re-justified.
  Concurrent persistence is now safe against transient sharing
  violations; lost-update races on the shared watchlist file remain
  possible (documented in the validator output) — acceptable, atomicity
  and corruption-tolerance are the guarantees.

### 11.9 Remaining bottlenecks / technical debt

1. `pandas.read_csv` + filesystem I/O still dominate cold loads
   (measured ~34 %, §11.1) — inherent; no further CSV optimisation
   justified (Phase 18 decision rule).
2. Watchlist/portfolio shared-file read-modify-write across workers can
   still lose an update (not corrupt) — a per-file lock or merge-on-
   write is a possible Sprint 11.7 follow-up if single-writer semantics
   are ever required.
3. `benchmarks/repro_concurrent_write.py` is a diagnostic harness kept
   for reproducing worker write races.

### 11.10 Validation

- Full suite: **2009 passed, 1 skipped, 0 failed** (11.5: 1982).
- Focused: `test_persistence` + `test_api` + `test_cache_correctness`
  + `test_sprint11_6` — 116 passed.
- CI gate: PASS at factor 3.0, FAIL at 0.01 (exit codes verified).
- Two-worker validation: RESULT PASS end-to-end (all phases).

---

## 12. Sprint 11.7 — Multi-worker state consistency, API profiling & CI warm-scan gate

Sprint 11.7 fixed the one remaining Sprint 11.6 debt — *cross-worker
shared JSON state can lose updates* — with a per-file transactional
lock, profiled `/api/analyze` under the real Docker two-worker
configuration, added a warm-scan regression gate to CI, and re-verified
multi-worker state under concurrency. **Full suite: `2024 passed, 1
skipped, 0 failed`** (+15 tests over 11.6). Trading logic, signal
formulas, ranking and atomic persistence semantics are unchanged.

### 12.1 Lost-update mechanism (reproduced, then fixed)

Two uvicorn workers each run `load → mutate → save` on shared JSON
stores. Interleaving `A reads v1 → B reads v1 → A writes v2A → B
writes v2B` leaves the file valid JSON but silently drops A's update.
Sprint 11.6's atomic writes + WinError 5 retry guarantee *no
corruption* but not *no lost updates*.

A deterministic reproducer was added first
(`tests/test_sprint11_7.py::TestLostUpdateReproducer`), then a real
Windows race was exposed while testing the fix: `os.open(O_CREAT|O_EXCL)`
on a lock file that exists and is momentarily open by another process
raises `PermissionError` (errno 13) **not** `FileExistsError` — the
sharing check fires before the existence check. `_acquire_file_lock`
now treats EEXIST and EACCES alike as contention (all other errnos
re-raise), which is what makes the lock reliable on Windows.

### 12.2 Affected stores (inventory)

| Store | File | Mutable | Multi-worker | Lost-update risk | Fixed |
| --- | --- | --- | --- | --- | --- |
| Alert history | `data/alerts/history.json` | yes | yes | yes | **yes** |
| Portfolio holdings | `portfolio.json` | yes | yes | yes | **yes** |
| Watchlist | `data/watchlist/watchlist.json` | yes | yes | yes | **yes** |
| Alerts centre (raw `json.dump`) | `data/alerts/*.json` | yes | low | low | watched |
| User settings | `~/.nepse/*.json` | yes | no | no | n/a |
| Trading journal | journal file | yes | no | no | n/a |
| UI notifications | notifications file | yes | no | no | n/a |
| Cache / walk-forward / versioning | various | no | no | no | n/a |

The three shared-file stores that the Docker `api` stage actually
mutates (alerts, portfolio, watchlist) are the ones routed through the
transactional helpers; the single-writer stores keep their existing
`save_json` paths (a plain read stays lock-free — no read pays for a
lock it cannot need).

### 12.3 Chosen concurrency model — per-file transaction lock (Option A)

Per the sprint's Critical Decision Rule, **Option A (per-file locking)**
was chosen over merge-on-write (Option B): the mutations are arbitrary
dict/list ops that cannot be generically merged, and a lock reuses
existing infrastructure with no new dependency (`filelock`/`portalocker`
are not installed — verified). Design:

```text
LOCK (in-process RLock)              # cheap: serialises scanner threads
  LOCK (cross-process lock file)     # os.open(O_CREAT|O_EXCL), atomic on
  |                                  # Windows + POSIX — no fcntl
  LOAD current state                 #
  APPLY mutation                     # the FULL transaction, per Phase 5
  ATOMIC temp write + os.replace     # existing guarantees preserved
UNLOCK
```

- **New public API in `src/utils/json_store.py`:** `locked_json(path)`
  (context manager) and `update_json(path, mutator, default, log_name=)`.
  A mutator may return `(new_state, result)`; the result is returned to
  the caller while the new state is persisted.
- **Lock file lifecycle:** `os.open(O_CREAT|O_EXCL)` — a crashed holder
  leaves a stale lock file which the next waiter breaks after
  `_LOCK_STALE_S = 8 s` (below the 10 s acquire timeout, so the breaker
  is reachable within one acquisition cycle — the Sprint 11.7 first-pass
  bug where 30 s stale > 10 s timeout made the breaker unreachable was
  fixed). Acquisition timeout 10 s, 20 ms retry.
- **Wired stores:** `watchlist.add_stock/remove_stock`, `alerts.
  history.update_state`/`save_alerts`, a new
  `portfolio.holdings.update_portfolio`, and the alert engine's
  `process_alerts`/`process_alert_batch` (which hold the lock for the
  whole batch load→mutate→save).
- **No deadlock path:** the engine passes `history=` to `update_state`
  while holding the lock, so `update_state` saves directly via
  `save_history` and never re-acquires the lock; the in-process RLock is
  re-entrant; lock ordering is consistent (RLock → file lock) in every
  process.
- **`_replace_atomic` unchanged** — the WinError 5/32 + EACCES/EBUSY
  retry from 11.6 is untouched; the lock adds transaction serialisation,
  it does not replace atomicity.

### 12.4 Concurrency validation — alerts / portfolio / watchlist

`tests/test_sleepy_save` injects a delay into the atomic replace to
widen the race window deterministically, then:

- **Watchlist:** 16 concurrent adds all survive; concurrent
  add/remove of one symbol never corrupts; duplicate add and missing
  remove are no-ops under contention.
- **Alerts:** 16 threads × 3 updates all survive; 8 concurrent
  `save_alerts` calls all survive; malformed/truncated/missing history
  still recovers.
- **Portfolio:** 8 concurrent position updates all survive.
- **Cross-process (uvicorn `--workers=2` equivalent):** two OS
  processes mutate disjoint 25-symbol ranges concurrently for each of
  watchlist / alerts / portfolio — **all 50 symbols survive in every
  store** (would be lost updates without the lock).

### 12.5 Docker two-worker stress test

`benchmarks/validate_workers.py` (extended in 11.7) spawns the exact
Dockerfile `api`-stage command (`uvicorn src.api.main:app --workers=2`)
against a temp real-corpus data dir with isolated state, and now also
spawns **two child processes** that call `process_alert_batch` +
`update_portfolio` directly against the shared state root while the API
keeps serving — the exact code path a second worker would take:

- All endpoints 200; 8-thread watchlist add/remove barrage all 200.
- Alert history: valid JSON, **20/20 symbols present**; portfolio:
  valid JSON, **20/20 holdings present** (lost updates would fail here).
- Persistence integrity: **no `*.tmp`, no `.corrupt.bak`, no leftover
  `*.lock`** (the scan now also flags stale lock sentinels, so a crashed
  worker fails loudly).
- Cross-worker `/analyze` responses identical.
- **RESULT: PASS** at both `--workers 2` and `--workers 1`.

### 12.6 `/api/analyze` — single vs two workers (measured)

20 real symbols, seeded corpus, warm repeats, `benchmarks/validate_workers.py`:

| Configuration | best | avg | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 worker | 32.7 ms | 68.0 ms | 43.6 ms | 166.2 ms | 598.6 ms |
| **2 workers (Dockerfile api command)** | 33.2 ms | 78.3 ms | 67.8 ms | 149.1 ms | 551.8 ms |

**Tail-source analysis (Phase 14):** the p95/p99 tail is **not** worker
scheduling, serialization, or filesystem contention — it is the cold
indicator-cache path and first-request-per-worker cost (Phase 13:
first call 55.9 ms → best repeat 36.0 ms, 1.6× warm-up; a request that
hits worker B's cold local LRU pays the full indicator chain). The
Sprint 11.6 measured tail (p99 168 ms at 200 symbols) shows the same
shape; absolute numbers vary with corpus size and machine state. JSON
serialization was measured at ~0.08 ms in 11.4 — not the tail.

### 12.7 Indicator cache under two workers

Per §10.3, process-local LRUs are retained — no shared cache was
introduced. Phase 15 confirms: values are identical across workers,
cross-worker L1 misses are harmless (one-time ~1 s cold start per extra
worker, §10.2), worker-local LRUs behave correctly, and no cross-worker
cache corruption occurs. The new profile does not change this
conclusion: misses only affect the first request per symbol per worker.

### 12.8 CI — warm-scan regression gate (Phase 16–17)

`benchmarks/ci_gate.py` now runs a third check on top of the 11.6
cold-load gate: a **warm-scan ratio** check on the same deterministic
20×300 synthetic corpus.

- `_scanner_times()` measures best-of-3 **cold** scan (scanner cache
  cleared + `gc.collect()` per rep) and best-of-3 **warm** scan (caches
  populated) with `workers=1`.
- Gate: `warm_scan_ms / cold_scan_ms <= WARM_SCAN_RATIO_MAX (0.5)` —
  i.e. a warm scan must be at least 2× faster than a cold scan.
  Measured on the reference machine: cold ~387–473 ms / warm ~21–29 ms,
  ratio **~0.04–0.08** — an order of magnitude of headroom, so normal
  runner variance cannot trip it, while an accidental scanner/indicator
  cache bypass (warm ≈ cold, ratio → 1.0) fails on any machine.
- **Rationale:** machine-independent (relative, both sides min-of-3),
  catches exactly what 11.6's load-only gate cannot — a broken cache in
  the *analysis* path, not just the CSV load.
- **Gate correctness (Phase 17):** `--warm-scan-ratio 0.5` → **PASS,
  exit 0**; `--warm-scan-ratio 0.001` → **FAIL, exit 1**, and the
  report identifies the failing metric with measured value vs threshold
  (`warm_scan=False, ratio 0.043, max 0.001`). The artifact (`benchmark-
  ci.json`) records `scanner_cold_ms`, `scanner_warm_ms`,
  `scanner_warm_cold_ratio`, `warm_scan_ratio_max` and the three checks;
  the CI workflow comment was updated to document the third check.

### 12.9 Performance validation (Phase 19) — worker scaling, real corpus

50/100/200 real symbols × workers 1/2/4 (3 cold + 5 warm reps,
`bench_scan_scale`), this machine, two back-to-back runs of the 50-symbol
matrix to gauge variance:

| Symbols | Workers | Cold p50 (run 1 / run 2) | Warm p50 | Peak mem |
| ------: | ------: | -----------------------: | -------: | -------: |
| 50 | 1 | 1174 / 1080 ms | 54–58 ms | 0.94 MiB |
| 50 | 2 | 857 / 785 ms | 34–38 ms | 0.97 MiB |
| 50 | 4 | 950 / 861 ms | 30–50 ms | 0.95 MiB |
| 100 | 1 | 1505 ms | 68 ms | 1.84 MiB |
| 100 | 2 | 1480 ms | 67 ms | 3.71 MiB |
| 100 | 4 | 1503 ms | 62 ms | 1.86 MiB |
| 200 | 1 | 3829 ms | 147 ms | 3.67 MiB |
| 200 | 2 | 3000 ms | 124 ms | 3.69 MiB |
| 200 | 4 | 3054 ms | 119 ms | 3.67 MiB |

**Honest reading:** cold p50 numbers carry the usual first-rep disk/
GC variance (run 1 vs run 2 spread up to ~10 %; the p95/p99 of the
*repetition sample* inside a run is inflated by the first cold rep —
that is the metric's design, not a regression). Warm p50s are stable
across runs and are the reliable comparison: at every size, workers 2–4
serve warm scans in ~30–125 ms vs 54–147 ms sequential. No claim of a
cold-path regression vs 11.6 is made from single noisy runs — the
11.6-vs-11.7 cold delta is within the documented ±30–60 % cold-scan
variance. Workers 1/2/4 remain fully deterministic (identical results,
0 skipped at every size). The default stays 4.

### 12.10 Trade-offs

- **Locking overhead** — each transactional mutation adds one `os.open`
  create + one unlink (sub-millisecond) plus the in-process RLock; on
  the warm scanner path (one alert-history read + one write per scan)
  the cost is invisible in the warm p50s above. Reads of immutable
  files never take the lock.
- **Lost-update guarantee vs merge semantics** — the lock serialises the
  whole transaction, so a *read* by a slow writer inside the lock cannot
  clobber another writer's update (verified by the sleepy-save tests).
  It does not change what a store holds, only that every committed
  mutation survives.
- **Stale-lock window** — a crashed worker's lock is broken after 8 s
  (next waiter); worst case an operation waits up to 10 s before a
  `TimeoutError` surfaces. No permanent deadlock is possible.
- **Process-local indicator caches** — intentionally independent per
  worker (§10.3, §12.7); the new API profile confirms misses are
  harmless, so no shared cache was introduced.
- **Docker** — two workers now share state *safely* (transactional
  locks) and *deterministically* (identical responses), while keeping
  the bounded one-time cold-start duplication of §10.2.

### 12.11 Remaining bottlenecks / technical debt

1. Cold CSV parse + disk I/O still dominate cold scans and cold
   `/api/analyze` (measured ~34 % of cold load in §11.1) — inherent;
   no further optimisation justified.
2. The alert engine's batch path takes the lock once per scan; a
   per-symbol lock-free read is not attempted (alerts history is
   single-file by design).
3. If the Docker `api` stage ever runs >2 workers *and* a profile shows
   cold-start cost mattering, revisit the shared indicator cache
   (§10.3) — not warranted today.
4. `repro_concurrent_write.py` remains as a diagnostic for worker write
   races.

### 12.12 Validation

- Full suite: **2024 passed, 1 skipped, 0 failed** (11.6: 2009).
- Focused: `test_sprint11_7` (15 tests: lost-update reproducer,
  json_store transaction, watchlist/alerts/portfolio thread + process
  survival) + `test_persistence` + `test_watchlist_manager` +
  `test_alerts_engine` + `test_api` + `test_portfolio_analyzer` — 53+
  green.
- Two-worker validation: **RESULT: PASS** at workers 2 and 1, including
  the new concurrent alert+portfolio child-writer leg and leftover-
  lock scan.
- CI gate: PASS (exit 0) at default thresholds, FAIL (exit 1) with an
  intentionally broken `--warm-scan-ratio`.
- Multi-worker state correctness: no lost updates, valid JSON, no
  tmp/corrupt/lock litter, atomic persistence intact — across all
  stores under 16-thread and two-process contention.

---

## 13. Sprint 11.8 — Watchlist ordering semantics, warm API gate & observability

### 13.1 Watchlist ordering semantics (audited, then locked in with tests)

A full consumer audit (`src/watchlist/manager.py`, `src/api/watchlist.py`,
`src/watchlist/scanner.py`, `src/data/service.py`, `src/sync/manager.py`,
`src/ui/pages/watchlist_page.py`) found that the **production mutation
path already preserves insertion order**: `add_stock` / `remove_stock`
route through `update_json` → `save_json(sort_keys=False)`, so the dict
key order survives persistence and reload exactly.  The only sorted
writer is the legacy bulk `save_watchlist(sort_keys=True)` (alphabetical),
used solely by tests — its deterministic-serialization behaviour is
asserted by `test_persistence.py::test_deterministic_serialization`, so it
was intentionally left unchanged.

**Supported semantics (documented contract):**

1. First insertion determines position.
2. Adding an existing symbol does not move it (duplicate add returns
   `False`).
3. Removing a symbol removes its position.
4. Re-adding a removed symbol appends it to the end.
5. Concurrent mutations produce a deterministic final state consistent
   with transaction serialization (each thread/process's own relative
   order is preserved).
6. Persistence/reload preserves the exact ordering.
7. Duplicate symbols never appear.

**Watchlist ordering is independent of scanner ranking order.** The
scanner ranks by composite score; the watchlist is a plain ordered
collection.  Consumers iterate the dict in key order (watchlist scan,
UI page), so insertion order is what they see.

Per the sprint rule "if already satisfied, add tests + documentation",
production `manager.py` was **not** rewritten — `tests/test_sprint11_8.py`
(15 new ordering/concurrency/observability tests) locks the contract in:
sequential add/dup/remove/re-add/reload, corruption recovery, 12-thread
unique-add survival, duplicate-add contention, add+remove mixes,
remove+re-add races, per-thread relative order under contention, and
cross-process relative-order preservation (two OS processes adding
interleaved ranges — each process's own commit order survives).

### 13.2 Concurrent ordering results

- 12 threads adding disjoint symbols: all survive, no duplicates, all
  `True` outcomes.
- 8 threads hammering the same symbol: exactly one entry, valid JSON,
  no `.tmp` / `.lock` litter.
- Remove+re-add races: deterministic single final entry.
- Two OS processes (uvicorn-worker equivalent): both processes' symbols
  present, each process's relative order preserved, valid JSON.
- Corruption recovery then re-add: ordering semantics still hold.

### 13.3 Warm /api/analyze CI gate

`benchmarks/ci_gate.py` gained a third machine-independent check
(`WARM_API_RATIO_MAX = 0.75`, CLI `--warm-api-ratio`).  `_api_analyze_times`
exercises the exact `/api/analyze` hot path (`analyze_stock`:
load_csv → indicator pipeline → score/signal → alert engine) in-process
with alert-history/portfolio persistence redirected to a temp dir.

- **Cold**: scanner cache + indicator cache cleared and GC before every
  repetition (true cold path, alert engine included).
- **Warm**: caches populated — repeated analysis hits the fingerprint
  indicator cache.
- Metrics: best-of-3 cold/warm totals, warm per-symbol p50/p95/p99,
  warm/cold best ratio.

**Measured (reference machine, 20×300 synthetic corpus):**

| Metric | Value |
| --- | ---: |
| Cold best / avg | 1258 / 1946 ms |
| Warm best / avg | 664 / 738 ms |
| Warm per-symbol p50 / p95 / p99 | 34.8 / 55.3 / 85.6 ms |
| Warm/cold ratio | 0.43–0.53 |

Why the ratio is ~0.5 and not ~0.05 (vs the scanner leg): `analyze_stock`
always runs the alert engine on both sides (the real API does too — a
history read/write per symbol), so warm is ~2×, not ~20×.  The 0.75
threshold sits comfortably above the measured 0.43–0.53 while still
tripping long before a cache bypass pushes the ratio toward 1.0.

**Verified behaviour:** `--warm-api-ratio 0.75` (default) → **PASS**
(exit 0, `checks.warm_api=True`); `--warm-api-ratio 0.001` → **FAIL**
(exit 1, `checks.warm_api=False`) with the ratio vs max printed so the
failing metric is identifiable.  The gate test in `test_sprint11_8.py`
asserts the machine-independent `warm_api` check (deliberately not the
full verdict, which includes the machine-dependent absolute baseline).

### 13.4 Docker observability (`/metrics`)

`src/api/metrics.py` now returns (counters only — no filesystem paths,
no secrets):

- `process`: pid + hostname — identifies which uvicorn worker answered
  (each worker is a separate process under `--workers=N`).
- `indicator_cache`: `indicator_cache.stats()` — hits, misses, entries,
  max entries, hit rate, enabled, pipeline version (is the worker-local
  cache warm?).
- `json_store`: `json_store.lock_stats()` — cross-process lock
  contention counters (`retries`, `stale_recoveries`, `timeouts`),
  updated only on the slow contention path so the hot path pays
  nothing.

`src/utils/json_store.py` `_acquire_file_lock` gained the counters and
`lock_stats()`.  This also **fixed a latent bug exposed by the new
stale-lock test**: the stale-age check mixed `time.monotonic()` (arbitrary
origin) with epoch `st_mtime`, so `age > _LOCK_STALE_S` was never true
and stale-lock recovery was dead code — a crashed holder would have
caused repeated lock timeouts forever.  Now uses `time.time()` (epoch,
matching `st_mtime`); fresh locks age ≈ 0, so a live holder is never
falsely broken.

### 13.5 State-integrity validation (`benchmarks/validate_workers.py`)

Added **Phase 15 — watchlist ordering semantics**, driven through the
real 2-worker API: add ORD_A/ORD_B/ORD_C → insertion order preserved;
duplicate add → no move; remove → slot freed; re-add → appended; and the
persisted `data/watchlist/watchlist.json` key order matches the served
order.  The `_scan_state` integrity scan was corrected to look for the
watchlist at its real location (`data/watchlist/watchlist.json` relative
to the API's CWD).

Two-worker run: **RESULT: PASS** — Phase 12 endpoints 200, Phase 16
latency recorded, Phase 13 warm repeats, Phase 14 watchlist barrage +
alert/portfolio child writers (20/20 symbols each), Phase 15 ordering
verified, no leftover `.tmp` / `.corrupt.bak` / `.lock`, cross-worker
responses identical.

### 13.6 Worker-scaling matrix (real corpus, seed 42)

| Symbols | Workers | Cold p50 | Cold p95 | Warm p50 | Warm p95 | Peak | Succ |
| ------: | ------: | -------: | -------: | -------: | -------: | ---: | ---: |
| 50      | 1       | 1456 ms  | 3726 ms  | 55.7 ms  | 82.2 ms  | 0.94 MiB | 50 |
| 50      | 2       | 1059 ms  | 1106 ms  | 42.7 ms  | 82.8 ms  | 0.97 MiB | 50 |
| 50      | 4       | 928 ms   | 1280 ms  | 31.5 ms  | 34.1 ms  | 0.95 MiB | 50 |
| 100     | 1       | 1602 ms  | 5489 ms  | 99.7 ms  | 186.8 ms | 1.84 MiB | 100 |
| 100     | 2       | 1682 ms  | 1751 ms  | 62.9 ms  | 67.9 ms  | 3.71 MiB | 100 |
| 100     | 4       | 1355 ms  | 1611 ms  | 56.2 ms  | 61.6 ms  | 1.87 MiB | 100 |
| 200     | 1       | 4419 ms  | 5598 ms  | 156.8 ms | 169.1 ms | 3.67 MiB | 200 |
| 200     | 2       | 3490 ms  | 4003 ms  | 136.8 ms | 264.8 ms | 3.69 MiB | 200 |
| 200     | 4       | 3721 ms  | 3963 ms  | 128.4 ms | 144.4 ms | 3.67 MiB | 200 |

No claim of a cold-path improvement vs Sprint 11.7 is made — cold-scan
variance is ±30–60 % run-to-run; the 11.7-vs-11.8 delta is within that
noise.  Warm-path stability is the focus: warm p50 at 200 symbols
(~128–157 ms) and perfect success/skip counts (no failures) across all
nine cells.  Worker count is unchanged (4 default) — no change is
justified by these measurements.

### 13.7 Concurrency

- Transaction serialization: every watchlist/alert/portfolio mutation
  commits atomically under the per-file cross-process lock (Sprint 11.7
  architecture unchanged), so final state is deterministic given the
  commit order and each writer's relative order is preserved.
- Ordering guarantees hold under 16-thread and two-process contention
  (see 13.2).
- Limitations: concurrent writers' *absolute* commit order is not
  predictable (that would require a distributed clock); only relative
  per-writer order is guaranteed.  Lock contention counters are
  process-local (each worker sees its own); there is no cross-worker
  shared counter (would need a shared store — out of scope).

### 13.8 Validation

- Full suite: **2042 passed, 1 skipped, 0 failed** (11.7: 2024).
- Focused: `test_sprint11_8` (18 tests) + `test_sprint11_7` +
  `test_watchlist_manager` + `test_persistence` + `test_performance` —
  96 green.
- Two-worker validation: **RESULT: PASS** including Phase 15 ordering.
- CI gate: PASS at defaults, FAIL with `--warm-api-ratio 0.001`
  (exit codes verified).

### 13.9 Remaining bottlenecks / debt

1. Cold CSV parse + disk I/O still dominate cold `/api/analyze` and cold
   scans (inherent; no further optimisation justified).
2. `/api/analyze` warm is only ~2× cold because the alert engine runs
   per symbol on both sides; a batch alert pass on the API path would
   narrow that gap but changes alert semantics — deferred.
3. Lock-contention counters are per-process; cross-worker aggregation
   would need a shared metrics store — not warranted today.
4. `save_watchlist` (sorted bulk writer) remains a footgun for ordering-
   sensitive callers; the mutation API is the documented ordering
   contract.  A future sprint could deprecate it.

### 13.10 Recommended Sprint 11.9

1. Deprecate `save_watchlist`'s silent alphabetical sorting (migrate
   tests, add an `ordered_save` or remove the legacy writer).
2. Extend the CI gate to a warm `/api/portfolio` check now that the
   warm-API pattern is established.
3. Consider an optional batch-alert pass for `/api/analyze` under a
   config flag (narrows the warm-vs-cold API gap without changing
   default alert semantics).
4. A file-watcher-based state monitor for the Docker stage could
   surface stale locks/litter proactively from the counters.

### 13.11 Test coverage summary

Sprint 11.8 added `tests/test_sprint11_8.py` (18 tests):

- `TestWatchlistOrdering` (7) — insertion order, no-move duplicate,
  remove, re-add append, reload persistence, no duplicates, corruption
  recovery.
- `TestWatchlistConcurrentOrdering` (5) — unique adds, duplicate
  contention, add+remove mixes, remove+re-add race, per-thread relative
  order.
- `TestWatchlistCrossProcessOrdering` (1) — two-process relative order.
- `TestJsonStoreLockObservability` (3) — counter shape, stale-break
  increments, contention retries + timeout.
- `TestMetricsObservability` (1) — `/metrics` process/indicator/json_store
  fields.
- `TestWarmApiGate` (1) — gate runs with `warm_api` check wired in.

---

## 14. Sprint 11.9 — Watchlist raw-writer contract, warm portfolio gate & API alert batching

### 14.1 Watchlist API — explicit ordering contract

**Canonical mutation path** (`add_stock` / `remove_stock`, via
`update_json`) preserves insertion order and is unchanged: first
insertion fixes the position, duplicate add does not move it, removal
frees the slot, re-add appends at the end, reload preserves exactly,
and duplicates never appear.  This was already true since Sprint 11.8.

**Raw writer fix** — `save_watchlist()` previously passed
`sort_keys=True`, silently re-ordering the dict *alphabetically* — a
semantic footgun inconsistent with the canonical mutation path.
Sprint 11.9 removed the silent sorting: the raw writer now persists
insertion order, matching the canonical path.  It has **zero production
callers** (tests only), so no caller behaviour changed.  `save_watchlist`
is documented as a *blind* write with no read-modify-write lock;
normal mutations should use `add_stock` / `remove_stock`.

Determinism still holds for the new contract: re-saving the *same*
dict (same insertion order) is byte-identical.  The legacy
`test_deterministic_serialization` assertion was updated accordingly.

**Ordering is independent of market ranking** — `rank_market` reorders
a copy; the watchlist dict and the `/watchlist` API response order are
never touched by scanner ranking.

### 14.2 Warm `/api/portfolio` CI gate

Fourth machine-independent gate check (`checks.warm_portfolio`).
Methodology mirrors the analyze gate: `_api_portfolio_times` seeds an
isolated portfolio file (one holding per corpus symbol) under
`_state_files`, patches `portfolio_analyzer.DATA_DIRECTORY`, and
measures best-of-3 cold (scanner + indicator caches cleared per rep)
vs best-of-3 warm `analyze_portfolio()`.

Measured on the reference machine (20x300 synthetic corpus, 20
holdings, best-of-3): cold best ~1308 ms vs warm best ~828 ms
(ratio ~0.63); gate runs observed 0.46-0.63.  Threshold:
`WARM_PORTFOLIO_RATIO_MAX = 0.75` — the same wide, machine-independent
envelope as `WARM_API_RATIO_MAX`, since `analyze_portfolio` analyzes
every holding through `analyze_stock` (alert engine runs on both
sides, so warm is ~1.5-2x, not ~20x).

CLI: `--warm-portfolio-ratio <max>` (default 0.75; lower = stricter).
PASS verified at the default; FAIL verified with `--warm-portfolio-ratio
0.001` (exit code 1, `checks.warm_portfolio=False`).  Deliberately
records cold/warm best + avg + ratio, not warm per-symbol percentiles:
portfolio is a whole-response latency, so a per-request percentile
adds no signal over the best-of-3 ratio for cache-bypass detection.

Artifact fields: `api_portfolio_cold_best_ms`, `api_portfolio_cold_avg_ms`,
`api_portfolio_warm_best_ms`, `api_portfolio_warm_avg_ms`,
`api_portfolio_warm_cold_ratio`, `warm_portfolio_ratio_max`,
`checks.warm_portfolio`.  The legacy `checks.absolute_baseline` key was
renamed `checks.cold_load` for the four-conceptual-check naming (only
stale artifact JSONs referenced the old name).

### 14.3 Analyze alert batching (`ENABLE_ANALYZE_ALERT_BATCH`)

**Feature flag** — `ENABLE_ANALYZE_ALERT_BATCH`, default `false`
(environment variable read in `src/config/__init__.py`).  When
**disabled** (default) every multi-symbol API path runs the exact
legacy per-symbol `process_alerts` engine, so responses are
byte-identical to pre-Sprint-11.9 behaviour.

**Why it exists** — profiling showed the per-symbol alert engine is the
majority of `analyze_stock` cost on the API hot path: for the 20x300
synthetic corpus, `analyze_stock` with alerts took ~1789 ms vs ~597 ms
without (~67% alert share), dominated by one history read + one atomic
write per symbol.  The single-symbol `/api/analyze` endpoint is *not*
a batch (a one-symbol request gains nothing) and is intentionally
unchanged.

**Internal abstraction** — `analyze_stock_batch(files,
use_batch_alerts=None)` in `src/engine/analyzer.py`.  When enabled it
analyzes every file with `with_alerts=False`, then calls the existing
`process_alert_batch` (Sprint 11.3) once — one history read + one
atomic write for the whole set — and attaches each symbol's
`new_alerts`.  Per-symbol analysis failures are isolated and returned
as error entries.  No new alert implementation was created.

**Wired paths** (both flag-gated):

- `/watchlist/scan` — `scan_watchlist()` resolves enabled symbols, runs
  `analyze_stock_batch`, and emits result/error entries interleaved in
  watchlist insertion order exactly like the legacy loop.
- `/portfolio` — `analyze_portfolio()` analyzes all present holdings
  via the batch path when enabled.

**Semantic equivalence** — regression tests prove enabled vs disabled
produce identical analysis signals, identical `new_alerts`, no
duplicate alerts on repeated runs, valid + non-duplicated history,
malformed-history recovery, concurrent-call survival, and
single-symbol backward compatibility.

**Measured performance** (synthetic 6x200, single run, informational):
legacy 6 reads + 6 writes ≈ 681 ms total vs batch 1 read + 1 write ≈
46 ms (~15x).  This is a *single-run* measurement — it is
informational only and does not gate CI (a noisy optimization
benchmark must not block CI, per Sprint 11.9 rules).

**Limitations** — in batch mode a holding whose analysis raises is
isolated and skipped in the portfolio response, whereas the legacy
path propagates the exception (HTTP 500); documented in the config
docstring.  Similarly, a per-entry `process_alert_batch` failure
(alert-engine exception) yields `new_alerts=[]` rather than an error
entry — an opt-in-mode divergence from the legacy raising behaviour.
The feature stays disabled by default regardless of benchmarks.

### 14.4 Docker `/metrics` observability

`benchmarks/validate_workers.py` gained **Phase 17** — `/metrics`
under the real `uvicorn --workers=2` deployment:

- HTTP 200 with `process.pid` (int) + `process.hostname`
- `indicator_cache` numeric counters (hits/misses/entries/max_entries
  >= 0) and `hit_rate` in [0,1]
- `json_store` lock counters (retries/stale_recoveries/timeouts >= 0)
- no secrets/filesystem paths in the payload (scans for
  token/secret/password/api_key/authorization)
- 8 repeated requests collecting the worker pids seen — tolerates load
  balancing (does not require a specific worker to answer)

Validation result: **RESULT: PASS** at `--workers 2` — both worker
pids observed `[3968, 9636]`, `indicator_cache entries=20
hit_rate≈0.74`, `json_store retries=8 stale=0 timeouts=0`, no secrets
or filesystem paths exposed.

### 14.5 Tests added (Sprint 11.9)

`tests/test_sprint11_9.py` — 24 tests:

- `TestWatchlistRawWriter` (5) — insertion-order raw writer, reload
  round-trip, canonical path unchanged, API-order == persisted-order,
  scanner ranking does not alter watchlist order
- `TestAnalyzeAlertBatch` (8) — flag defaults disabled, batch reuses
  `process_alert_batch`, enabled==disabled signals+alerts, no
  duplicate alerts, valid history, malformed-history recovery,
  concurrent survival, single-symbol backward compat
- `TestWatchlistScanBatchEquivalence` (2) — scan enabled==disabled,
  error entries preserved
- `TestPortfolioBatchEquivalence` (1) — portfolio enabled==disabled
- `TestApiAlertBatchBenchmark` (1) — read/write counts (legacy 6/6,
  batch 1/1) + alert equivalence
- `TestMetricsCorrectness` (4) — numeric counters + consistency,
  cache activity before/after, lock contention, safe when indicator
  cache disabled
- `TestWarmPortfolioGate` (3) — portfolio-times shape, full-gate four
  checks + warm_portfolio PASS, absurdly-strict FAIL

`tests/test_persistence.py` — `test_deterministic_serialization`
updated to the new insertion-order contract.

### 14.6 Performance vs Sprint 11.8

Worker matrix (real corpus, seed 42) — cold p50 / warm p50 ms:

| Symbols | workers 1 | workers 2 | workers 4 |
| --- | --- | --- | --- |
| 50  | 1522 / 88  | 1391 / 46  | 1362 / 138 |
| 100 | 2474 / 71  | 2024 / 87  | 1959 / 66  |
| 200 | 5238 / 197 | 4142 / 182 | 3966 / 146 |

All 200 symbols processed successfully with 0 skips across all worker
counts — scanner determinism is preserved.  The headline Sprint 11.9
gains are the new warm-portfolio regression gate and the measured API
batch-alert path; no scanner speedup is claimed from these numbers
(single-run cold p50 varies with machine noise, consistent with
previous sprints' documented variance).  Warm-path behaviour and the
CI gate are the stable, reproducible deliverables.

### 14.7 Remaining debt / Sprint 12.0 candidates

- Batch alerts stay opt-in; an equivalence-preserving batch pass in
  the single-symbol `/api/analyze` is impossible by construction and
  not attempted.
- `save_watchlist` remains a raw (non-transactional) writer; a
  follow-up could deprecate it in favour of `add_stock`/`remove_stock`
  everywhere, but no production caller exists today.
- A warm `/api/portfolio` per-request percentile could be added if the
  ratio gate ever needs finer granularity.
- Consider a module-scoped gate fixture in the test suite to avoid
  running the full gate three times (Sprint 11.8 + 11.9 PASS/FAIL).
  *Delivered in Sprint 12.0 §15.6.*

## 15. Sprint 12.0 — Production batch scanning, portfolio percentiles,
cross-worker observability & writer deprecation

### 15.1 Production market-scan batching

The production market-scan path is
``DataService.scan_market()`` → ``_csv_scan()`` (``src/scanner/engine.py``)
→ ``scan_market()``.  That path has processed alert state through
``process_alert_batch`` (one history read + one atomic write per scan)
since Sprint 11.3 — it is the *proven* batch path, and it is
**unconditional** in the scanner (Sprint 11.3 made it the default).

Sprint 12.0 therefore did **not** rewrite the scanner.  It locked the
behaviour in with regression tests (``tests/test_sprint12_0.py``):

- functional: empty market, single/multi symbol, skipped symbols,
  malformed CSV, one/all symbols raising, deterministic result
  ordering, deterministic alert ordering;
- persistence: a multi-symbol scan performs exactly **1 history read
  + 1 history write** (verified by dual-namespace counters), no
  duplicate alerts on rescan, valid JSON, corruption recovery, no
  ``.tmp`` / ``.lock`` litter;
- backward compatibility: ``scan_market()``, ``scan_watchlist()`` and
  ``analyze_stock()`` callers are unchanged.

The ``ENABLE_ANALYZE_ALERT_BATCH`` flag (default **false**) is **not
obsolete** and was kept: it gates the *API-level* multi-symbol helper
``analyze_stock_batch()`` (watchlist scan, portfolio), which is a
different path from the scanner's internal batching.  When disabled the
helper runs the exact legacy per-symbol ``process_alerts`` path, so
those endpoints stay byte-identical to pre-Sprint 11.9 behaviour.

### 15.2 Legacy vs batch equivalence

``benchmarks.pipeline.bench_scan_alert_equivalence()`` runs the two
alert paths (legacy per-symbol ``analyze_stock`` loop vs
``analyze_stock_batch(use_batch_alerts=True)``) over the same
corpus from a fresh history and reports four flags:

- ``analysis_equivalent`` — stable analysis fields (symbol, signal,
  score, confidence) identical;
- ``alerts_equivalent`` — complete ``new_alerts`` payloads identical;
- ``ranking_equivalent`` — ``rank_market`` output identical;
- ``errors_equivalent`` — skipped/error entries identical.

Measured on the 6×200 synthetic corpus: all four flags **True**, with
legacy doing 6 history reads + 6 writes vs the batch's 1 + 1.
(Note: the ranking leg ranks the normalized comparison rows, so it is
a sanity check on top of analysis equivalence, not an independent
full-payload ranking comparison.)

### 15.3 Market-scan alert-path performance (real corpus)

``benchmarks.pipeline.bench_scan_alert_batch()`` (wired into
``benchmarks.runner`` as ``scan_alert_batch``) measures legacy vs batch
cold/warm percentiles, history read/write counts and peak memory.
Real-corpus 50/100/200 runs (seed 42, cold_reps=2, warm_reps=3):

| symbols | batch cold p50 | batch warm p50 | legacy cold p50 | legacy warm p50 | batch reads/writes |
|---|---|---|---|---|---|
| 50  | 3206 ms | 393 ms | 7313 ms | 2770 ms | 2/2 (cold), 3/3 (warm) |
| 100 | 4380 ms | 679 ms | 11531 ms | 4544 ms | 2/2 (cold), 3/3 (warm) |
| 200 | 7381 ms | 1097 ms | 24159 ms | 11658 ms | 2/2 (cold), 3/3 (warm) |

All symbols analysed successfully, 0 failures, peak memory
1.8 / 3.5 / 6.8 MiB.  ``cold_reps=2`` ⇒ the cold read/write totals are
2 reads + 2 writes for the batch (one per rep) vs 2×N for legacy;
warm reps similarly.  The counters are **per-phase totals** (not
per-scan) — batch stays O(reps) while legacy scales with reps×symbols.
These are single-run measurements for the table; the CI gate remains
the ratio-based, repeated-measurement check.

### 15.4 Warm /api/portfolio percentiles

``benchmarks.ci_gate._api_portfolio_times`` now records **warm
per-holding p50/p95/p99** alongside the existing cold/warm best/avg.
The artifact gains ``api_portfolio_warm_p50_ms`` / ``p95_ms`` /
``p99_ms``; the CLI prints them.  These are **descriptive**
measurements only — the machine-independent regression gate remains
``checks.warm_portfolio`` (``warm_cold_ratio <= 0.75``).

Gate run on the reference machine (20×300 synthetic, 20 holdings):
portfolio cold best 1689 ms / warm best 899 ms (ratio 0.532), warm
per-holding p50/p95/p99 = 56.0 / 273.7 / 298.9 ms.  PASS at the
default threshold; FAIL verified with ``--warm-portfolio-ratio 0.001``
(exit code 1).

### 15.5 Cross-worker metrics aggregation

No Prometheus / StatsD / Redis backend exists in the project (audited;
SQLite is portfolio-storage only), so the sprint's own rule applied:
a minimal stdlib-compatible solution on existing infrastructure.

``src/utils/worker_metrics.py`` adds best-effort aggregation:

- each worker self-reports a small record (pid, hostname, last_seen,
  local indicator-cache and JSON-store counters) into a shared JSON
  store via the existing transactional ``update_json`` (atomic,
  corruption-tolerant, cross-process lock);
- writes are throttled per process (once per 5 s) so metrics can never
  become a write hot-spot or a lock-contention source;
- ``/metrics`` (``src/api/metrics.py``) enriches the worker-local
  payload with ``workers`` (active/known, TTL-based liveness,
  default 60 s via ``WORKER_METRICS_TTL_S``) and ``aggregate`` (summed
  hits/misses, hit_rate, lock retries/stale/timeouts) blocks;
- worker-local blocks are preserved unchanged;
- **best-effort**: any failure degrades to the local-only payload and
  never breaks the API; stale records are tolerated (never deleted,
  never error); no secrets or filesystem paths are ever written.

Aggregation answers: how many workers are active, which pids, is the
cache warm, the aggregate hit rate, and whether JSON locks are
contending / stale / timing out.

### 15.6 Docker 2-worker /metrics validation

``benchmarks.validate_workers.py`` gained **Phase 18**: under the real
``uvicorn --workers=2`` deployment it verifies ``workers.active >= 1``,
``workers.known`` non-empty, aggregate counters non-negative,
hit_rate in [0,1], count consistency (aggregate hits/misses >= the
serving worker's local counts), no secrets, and no state litter
(``.tmp`` / ``.corrupt.bak`` / ``.lock``) from metrics writes.

Result: **PASS** — both worker pids observed ([6500, 12536]),
``workers.active=2``, aggregate hits=68 / misses=39 (hit_rate 0.636),
lock retries=15, stale=0, timeouts=0, no secrets/paths, no litter.

### 15.7 Watchlist writer deprecation & CI runtime

- ``save_watchlist()`` now emits a ``DeprecationWarning`` pointing at
  the canonical ``add_stock()`` / ``remove_stock()`` / ``update_json``
  path.  Repository audit confirmed **zero production callers** (tests
  only), so the raw writer is retained for tests/bulk restore and
  documented for removal.  Existing tests were updated to assert the
  warning via ``pytest.warns(DeprecationWarning)``.
- **CI gate runtime**: the full benchmark gate previously ran three
  times in the suite (test_sprint11_8 ×1, test_sprint11_9 ×2).  A
  session-scoped ``ci_gate_artifact`` fixture (``tests/conftest.py``)
  now runs it **once per session**; the PASS test asserts on the shared
  artifact and the FAIL-path test derives its verdict from the same
  real measured ratio (the check arithmetic ``ok = ratio <= max`` is
  pure), so no assertion is weakened.  Deterministically, the fixture
  removes 2 of the 3 full gate runs (each ~2-4 s on the reference
  machine); the focused suite completed in ~71-96 s depending on
  machine noise.  (A controlled before/after wall-clock measurement
  was not captured, so the saving is stated as a deterministic run
  count, not a precise delta.)

### 15.8 Trade-offs

- Batch alerts remain **opt-in** for the API-level helper
  (``ENABLE_ANALYZE_ALERT_BATCH=false`` default); the scanner batches
  unconditionally by design (proven since Sprint 11.3).  No error-
  semantics change on the default path.
- Cross-worker metrics are best-effort observability: the shared store
  can lag under the write throttle (a worker's stored record may trail
  its live counters for up to 5 s), which is why validation checks
  counts, not rates.
- The warm portfolio percentiles are descriptive; the ratio gate stays
  the CI check (no new flaky threshold).
- No new dependencies; no global mutable state pretending to be shared;
  no speculative optimization.  All changes preserve atomic
  persistence, determinism, exception isolation and backward
  compatibility.

## 16. Sprint 12.1 — Analyze p99 gate, Streamlit Metrics dashboard & 500+ profiling

Sprint 12.1 adds a warm `/api/analyze` absolute p99 regression tripwire,
a Streamlit Metrics dashboard that consumes the existing `/metrics`
endpoint, a 500+ symbol `process_alert_batch` scaling benchmark, and
the removal of the deprecated `save_watchlist()` writer.

### 16.1 Warm /api/analyze p99 CI gate

The gate's `_api_analyze_times` already recorded warm per-symbol p50 /
p95 / p99 (descriptive).  Sprint 12.1 wires a **configurable absolute
tripwire** on the p99 into the gate verdict:

```python
ANALYZE_P99_MAX_MS = 2000.0   # benchmarks/ci_gate.py
```

- **Check** `checks.warm_api_p99`: `api_analyze_warm_p99_ms <=
  analyze_p99_max_ms`.  Configurable via `run_gate(analyze_p99_max_ms=...)`
  and the `--analyze-p99` CLI flag (e.g. `--analyze-p99 500` for a
  stricter tripwire).
- **Measured baseline** (reference machine, 20x300 synthetic corpus,
  best-of-3): cold best ~1164-1171 ms total (avg ~1436 ms); warm best
  ~651-761 ms
  total; warm per-symbol p50 ~32-38 ms, p95 ~42-100 ms, p99 ~178-285
  ms across runs.  The default 2000 ms leaves ~2.3-7x margin over a
  slow CI runner (1.5-3x slower than reference).
- **Statistical note**: with 60 warm samples (20 symbols x 3 reps) the
  p99 is `sorted[59]` — the single worst sample — so this check is by
  construction a *"warm max < threshold"* severe-regression tripwire,
  not a stable tail statistic.  A single GC pause can lift it, which is
  exactly why the threshold is generous and the machine-independent
  ratio checks (`warm_api`, `warm_portfolio`, `warm_scan`) remain the
  primary cache-bypass noise detectors.
- **Methodology**: identical to the Sprint 12.0 gate — deterministic
  synthetic corpus in a private temp dir, alert/portfolio state
  redirected via `_state_files`, in-process `analyze_stock` hot path,
  no network, no production state.
- **Verified**: PASS at default (exit 0), FAIL at `--analyze-p99 0.001`
  (exit 1) with `checks.warm_api_p99=false`.  Regression tests in
  `tests/test_sprint12_1.py` cover the artifact field, the default-pass
  and the strict-fail wiring (fast 4x100 corpus, single gate run).

### 16.2 Streamlit Metrics dashboard

A new `Metrics` page (`src/ui/pages/metrics_page.py`, registered in
`app.py` under System) renders the backend `/metrics` snapshot over
HTTP (stdlib `urllib`, `API_BASE_URL` from config):

- **System overview** — active worker count, total workers observed,
  aggregate indicator-cache hit rate + hits/misses, lock retries /
  stale recoveries / timeouts, and metrics freshness (age of the
  newest worker report).
- **Worker breakdown** — a table of every observed worker: PID,
  hostname, active/inactive status, last-seen age.
- **Visualizations** — Streamlit-native bar chart of aggregate vs
  serving-worker cache counters.
- **Failure handling** — the page never crashes: unreachable backend,
  non-JSON / non-object payloads, no workers, stale workers and
  malformed records all render a clear user-facing status (pure
  `fetch_metrics` / `summarize` helpers are unit-tested against every
  case).
- **Schema limitation** (documented): `/metrics` `workers.known`
  records carry only pid/hostname/last-seen — per-worker cache and
  lock counters are **not** exposed per worker by the endpoint (only
  the serving worker's local block and the aggregate), so the worker
  table shows identity/liveness while counters are shown aggregate +
  serving-worker-local.  No secrets or filesystem paths are displayed.

### 16.3 500+ symbol profiling (process_alert_batch)

`benchmarks/pipeline.py::bench_alert_batch_scale` runs the exact
`bench_scan_alert_batch` measurement at 500 / 750 / 1000 symbols on a
**synthetic** corpus (the real scraped corpus ships 287 CSVs, so 500+
must be synthetic — labelled `corpus: synthetic`).  Each size runs in
its own temp dir under `_state_files` isolation; caches are cleared
between sizes.  `benchmarks/runner.py` gained an opt-in `--scale 500
750 1000` flag (the default runner stays fast).

Measured (reference machine, 300 rows/symbol, cold reps 2 / warm reps 3):

| Symbols | batch cold p50 | batch warm p50 | reads/writes | failures |
|---|---|---|---|---|
| 500 | 12 840 ms | 12 804 ms | 2 / 3 | 0 |
| 750 | 19 264 ms | 19 166 ms | 2 / 3 | 0 |
| 1000 | 26 040 ms | 26 327 ms | 2 / 3 | 0 |

Per-symbol cold cost stays flat (~25-26 ms/symbol at every size), so
the classifier reports **approximately-linear** scaling — the batch
path does not degrade super-linearly as symbol count grows.  Peak
memory and success counts are recorded in the benchmark JSON; all
symbols analysed, 0 failures at every size.  No production state is
touched (verified by a test that snapshots the `data/alerts` file set
name/size/mtime before and after).

**Bottleneck note** — per Sprint 12.1 Phase 3, no production code was
changed for speed: at these sizes the batch path is dominated by the
same cold CSV parsing + first-time indicator calculation that limits
the 50/100/200-symbol runs; `process_alert_batch` itself contributes
one read + one write regardless of symbol count.  A profiler-driven
investigation of the indicator pipeline (not the alert path) would be
the next candidate, but no safe optimisation is justified without
further measurement.

### 16.4 Watchlist writer removal

`save_watchlist()` (deprecated in Sprint 12.0) was **removed** in
Sprint 12.1: the repository audit found zero production callers (tests
only).  `src/watchlist/manager.py` now exposes only the canonical
transactional path — `add_stock()` / `remove_stock()` / `load_watchlist()`
backed by `update_json` (cross-process locked, insertion-order
preserving).  Tests were reworked to preserve the underlying guarantees
(atomic persistence, no temp litter, deterministic serialisation via
`update_json`; the lost-update reproducer now uses the raw
`save_json` primitive that the old writer wrapped).  An `ast`-based
test asserts no code references remain anywhere.

### 16.5 CI gate behaviour

The gate now runs **six** checks; the fifth ratio check
(`warm_portfolio`) and the new absolute p99 tripwire
(`warm_api_p99`) are both wired into the verdict conjunction.  The
`--analyze-p99` flag is machine-independent only in spirit — it is an
absolute threshold — so the default is deliberately generous and the
ratio checks remain the noise detectors.  Full suite: **2106 passed, 1
skipped, 0 failed** (Sprint 12.0: 2094 passed).

### 16.6 Trade-offs

- The p99 tripwire is absolute and can false-fail on an unusually
  noisy single sample only if that sample exceeds 2000 ms — far beyond
  any measured reference value; ratio checks carry the primary
  detection duty.
- The metrics dashboard depends on the FastAPI backend being reachable
  (the page degrades to a clear status message when it is not); the
  in-process Streamlit pages remain fully functional regardless.
- 500+ profiling uses synthetic data by necessity (287 real CSVs);
  real-corpus scaling above 287 symbols requires a larger scrape.
- `save_watchlist` removal is a deliberate breaking change for any
  external caller — none exist in the repository.
---

## 17. Sprint 12.2 — Indicator cold-path profiling, Streamlit audit & corpus expansion

Sprint 12.2 is an **investigation sprint**: profile the indicator
pipeline's cold path, baseline the Streamlit app / Metrics page, add a
metrics-refresh improvement only if measurement justifies it, and
investigate expanding the real NEPSE corpus.  The sprint's core rule —
*measure first, optimise only when evidence justifies it* — meant most
of the production code stayed untouched.  **Full suite: 2127 passed, 1
skipped, 0 failed** (Sprint 12.1: 2106 passed); CI regression gate
PASS (see §17.7).

### 17.1 Indicator cold-path profile (Phase 1)

**Workload:** deterministic synthetic corpus (seeded `write_csvs`, 500
rows/symbol) — single-symbol cold `analyze_stock`, plus full cold
`scan_market(workers=1)` at 50 / 200 / 500 symbols.  Sequential
`workers=1` keeps every call in the profiled main thread (the parallel
path would scatter profile records across worker threads).  All
workloads run under `_state_files` isolation — production
`data/alerts/history.json` is never touched (asserted in
`tests/test_sprint12_2.py` by file-set snapshot).

**Environment:** Windows 10, Python 3.12.8, cProfile + pstats.
Profiler overhead inflates absolute wall times (~3.9× at 500 symbols:
99.1 s profiled vs ~26 s unprofiled per the Sprint 12.1 scaling figure);
percentages are relative and comparable.

**Artifacts:** `benchmarks/results/profile/profile_summary.json`
(JSON), `single_symbol.prof` / `scan_{50,200,500}.prof` (pstats dumps),
`single_symbol.txt` / `scan_*.txt` (human-readable top-30 reports).
Re-run with `python -m benchmarks.profile_cold_path --sizes 50 200 500
--rows 500`.

**Top functions by cumulative time (500-symbol cold scan, 99.14 s total):**

| Function | Module | Calls | tottime (s) | cumtime (s) | % total |
| --- | --- | ---: | ---: | ---: | ---: |
| `load_csv` | `src/loaders/csv_loader.py` | 500 | 0.14 | 52.41 | 52.9 % |
| `analyze_dataframe` | `src/engine/analyzer.py` | 500 | 0.03 | 44.86 | 45.2 % |
| `_read_csv_robust` | `src/loaders/csv_loader.py` | 500 | 0.06 | 42.91 | 43.3 % |
| `read_text` → `_io.open` | `io` / `~` | 500 | 38.06 | 38.07 | 38.4 % |
| `__setitem__`/`insert` (pandas) | `pandas.core.indexing` | 8 000 | 0.18 | 14.15 | 14.3 % |
| `add_momentum_indicators` | `src/indicators/momentum.py` | 500 | 0.03 | 10.42 | 10.5 % |
| `add_volume_indicators` | `src/indicators/volume.py` | 500 | 0.01 | 10.17 | 10.3 % |
| `add_volatility_indicators` | `src/indicators/volatility.py` | 500 | 0.01 | 9.50 | 9.6 % |
| `add_moving_averages` | `src/indicators/moving_average.py` | 500 | 0.04 | 6.18 | 6.2 % |
| `_build_analysis` | `src/engine/analyzer.py` | 500 | 0.05 | 5.36 | 5.4 % |

(Truncated at 10 rows; the full top-30 is in `scan_500.txt`.  The pandas
`__setitem__`/`insert` chain — column insertion machinery inside
`analyze_dataframe` — spans the 14.3 % `__setitem__` row plus the
`_set_item`/`_set_item_mgr` rows beneath it.)

**Single-symbol cold profile** (1 symbol × 500 rows, 0.194 s profiled):
`load_csv` 61.8 % cumtime (of which file read ~46 %), `analyze_dataframe`
34.2 % — the same shape as the multi-symbol run.

**Identified bottleneck:** the cold path is dominated by (1) **CSV file
I/O** (`read_text`/`_io.open`, ~38 % of the 500-symbol run — inherent
disk/OS read cost, already cached on warm scans) and (2) **pandas
rolling/ewm library code** inside the indicator chain (the four
`add_*_indicators` families ≈ 36 % cumtime) plus the pandas
block-manager column-insertion overhead (~14 %).  `_build_analysis`
(score/signal/trade-plan/risk/alerts tail) is only ~5 %.

**Per-symbol scaling (cold, profiled):** 50 → 14.81 s, 200 → 47.41 s,
500 → 99.14 s.  Per-symbol cost is flat (~0.20 s/symbol profiled),
confirming linear scaling — no hidden O(n²) term in the pipeline.

**Optimisation recommendation:** none of the identified costs are
application-level inefficiencies.  CSV I/O is unavoidable (and already
mitigated by the scanner cache + pandas C-parser fast path from Sprint
11.6); the rolling/ewm work is efficient vectorised library code; the
column-insertion overhead is pandas-internal block management with no
safe application-level reduction that would not change values.  **No
optimisation implemented** — the measurement justifies leaving the
production pipeline unchanged.

### 17.2 Streamlit application baseline (Phase 2)

Measured on this machine (Windows 10, Python 3.12.8, Streamlit 1.60.0):

- **`import streamlit`**: ~24.3 s (one-time process cost; the running
  app pays this once at startup).
- **`import src.ui.pages.metrics_page`**: ~19.1 s (dominated by the
  streamlit + pandas transitive imports, one-time; the page module
  itself is small).
- **`GET /metrics` endpoint** (in-process TestClient, 10 warm samples):
  p50 **9.7 ms**, p95 **9.9 ms**, best 8.6 ms, avg 9.4 ms.  The
  endpoint is cheap; the page's cost is the *count* of requests, not
  any single request.
- **Rerun behaviour (documented, not assumed):** the Metrics page
  called `fetch_metrics()` — one HTTP `GET /metrics` — on **every**
  Streamlit rerun before this sprint.  Reruns fire on every widget
  interaction, every navigation click, and every auto-refresh tick
  (`10 sec` / `30 sec` / `1 min` / `5 min` modes in `src/ui/state.py`).
  With the backend down, each rerun also blocked up to the 3 s fetch
  timeout.
- **Navigation:** page routing in `app.py` lazy-imports the selected
  page module inside the `try` block, so switching pages does not
  re-import heavy modules; each navigation does trigger a full rerun of
  the newly selected page (which re-runs its data fetch).

**Expensive operations identified:** per-rerun `/metrics` HTTP requests
are the only per-rerun network cost on the Metrics page.  No repeated
file reads or re-imports occur on navigation.  The page's own chart /
table rendering is trivial relative to the fetch.

### 17.3 Metrics refresh (Phase 3) — implemented, measured justification

**Previous behaviour:** one `GET /metrics` per rerun, unbounded by time
— a widget click, a nav, or an auto-refresh tick all re-fetched.

**Change:** `src/ui/pages/metrics_page.py` wraps the fetch in a short
TTL cache (`st.cache_data`):

```python
METRICS_FETCH_TTL_S = float(os.getenv("METRICS_FETCH_TTL_S", "10"))  # src/config

@st.cache_data(ttl=METRICS_FETCH_TTL_S, show_spinner=False)
def _cached_fetch_metrics(base_url, timeout): ...
```

- **TTL:** 10 s default (`METRICS_FETCH_TTL_S`, env-overridable).
- **Successes** are served from cache for up to 10 s — a burst of
  reruns (interactions, auto-refresh ticks) issues at most one request
  per 10 s window.
- **Errors are never cached** (verified: `st.cache_data` does not store
  raised exceptions), so an outage recovers on the next rerun instead
  of being pinned — the page keeps its existing graceful "metrics
  unavailable" path.
- **Manual refresh:** a "🔄 Refresh now" button calls
  `_cached_fetch_metrics.clear()` then `st.rerun()`; the failure-path
  "Retry" button also clears explicitly.
- **Freshness tradeoff:** the snapshot can be up to 10 s old on the
  page.  Worker metrics self-report on a 5 s throttle with a 60 s
  liveness window (§16.2), so a 10 s snapshot is comfortably within the
  observability granularity — no user-visible staleness.

**Request reduction (measured behaviour, not a wall-clock claim):** the
cache hit is deterministic per Streamlit's `st.cache_data` semantics
(verified out-of-runtime with a short-TTL helper in
`tests/test_sprint12_2.py`): within a 10 s window the underlying
`fetch_metrics` runs once regardless of rerun count; after TTL expiry
(or `clear()`) it runs again.

**Why not WebSocket/SSE push?** The project has no existing metrics
push infrastructure to reuse (the WebSocket module is the live-market
feed, not metrics), the endpoint costs ~10 ms, and an operational
metrics dashboard does not need sub-second freshness — a 10 s TTL is
adequate.  Building push would add infrastructure for zero measured
benefit.  Per the sprint's Phase 3 mandate, **caching was sufficient
and the simplest reliable solution.**

### 17.4 Real NEPSE corpus (Phase 4) — no safe expansion, audit + report

**Current corpus:** `data/raw/` ships **287 CSV files**.  The Sprint
12.2 corpus audit (`benchmarks/corpus_audit.py`, read-only, stdlib csv)
reports:

| Metric | Value |
| --- | ---: |
| Files | 287 |
| Usable (>= 5 rows, OHLCV schema) | 280 |
| Unusable | 7 |
| Duplicate symbols | none |
| Too-few-rows files | 7 (gbblpo, ilbsp, jbblpo, kmcdbp, matrip, mlblpo, mslbp) |
| Non-numeric rows | 1 (`sample.csv` — the demo file, excluded from scans; quoted thousands separators in Volume, so it is a *format* flag, not a bad-price flag) |
| OHLC sanity violations (High < Low etc.) | 0 |
| "Order" flags | 286 — scraped files are newest-first; `load_csv` sorts ascending (the audit reports raw order only) |

**Missing symbols / active companies:** the repository has no committed
canonical company list — the "company list" is whatever the live
provider returns (`APIProvider.get_live_quotes`), so active-vs-missing
membership cannot be enumerated offline.

**Source limitations (measured at audit time):** both configured
history sources were unreachable:

- `nepse_scraper` (`https://nepseapi.surajrimal.dev/api/v1`) → **HTTP
  403 Forbidden**
- `github_datasets` / `nepse_client`
  (`https://shubhamnpk.github.io/yonepse/data`) → **HTTP 404 Not Found**

**Decision:** per the sprint's no-fabrication rule, the corpus was **not
expanded** — no real data could be fetched safely, and synthetic
history would be fabrication.  Synthetic 500/750/1000-symbol benchmarks
remain clearly labelled `corpus: synthetic` (unchanged from 12.1).

**Recommended next step:** `docs/SCRAPER_MIRROR.md` describes the
private GitHub-Actions mirror (`nepse-scraper-mirror`) that owns the
daily scrape; pointing `DATA_SERVICE_API_URLS["github_datasets"]` at
the mirror's raw `docs/api` path (once it is live) is the intended
source for a real-corpus expansion.  Until then the real corpus is 287
files and that is a documented source limitation, not a defect.

### 17.5 Production optimisations (Sprint 12.2)

| Before | Evidence | Change | After | Improvement | Risk |
| --- | --- | --- | --- | --- | --- |
| Metrics page fetched `/metrics` on every rerun (unbounded requests, 3 s block per rerun when backend down) | §17.2 baseline: 1 request/rerun, endpoint ~10 ms | `st.cache_data(ttl=10 s)` wrapper + manual refresh button | Requests bounded to ~1/10 s window; errors still retried next rerun | Request-count reduction per rerun burst; outage block bounded | None material — successes cached 10 s (freshness within observability granularity), errors never cached |

No other production change was made: the indicator pipeline, CSV
loader, scanner, alert engine and data service are unchanged, because
the Phase 1 profile showed the cold path is dominated by unavoidable
file I/O and efficient pandas library code.

### 17.6 Remaining bottlenecks (measured only)

1. **Cold CSV file I/O** — ~38 % of a cold 500-symbol scan
   (`read_text`/`_io.open`).  Inherent disk cost; warm scans bypass it
   via the scanner cache.
2. **Pandas rolling/ewm indicator chain** — ~36 % cumtime across the
   four `add_*_indicators` families.  Vectorised library code; no safe
   application-level reduction.
3. **Pandas block-manager column insertion** (`__setitem__`/`insert`,
   ~14 %) — pandas-internal; no value-preserving application-level fix.
4. **Metrics page fetch is still HTTP to the backend** — bounded by the
   10 s TTL, but the page needs the FastAPI server running (unchanged
   from 16.2).

### 17.7 Validation (Sprint 12.2)

- Focused: `tests/test_sprint12_2.py` — 20 passed (profiling infra
  structure/determinism/isolation, metrics cache success/error/clear/
  TTL/fetch-parsing, corpus audit schema/duplicates/rows/OHLC/order/
  non-numeric/ragged-row/read-only).
- Full suite: **2127 passed, 1 skipped, 0 failed** (Sprint 12.1:
  2106 passed — +21 tests).  Sprint 12.0/12.1 focused suites
  (`test_sprint12_0/1/2`) — re-run green after the final Sprint 12.2
  test additions (the 60-passed figure was measured before the last two
  tests landed).
- CI regression gate (`python -m benchmarks.ci_gate --factor 3.0`):
  **PASS** — all six checks green (cold_load, warm_speedup, warm_scan,
  warm_api, warm_portfolio, warm_api_p99).  Warm /api/analyze
  per-symbol p99 measured 172.3 ms vs the 2000 ms tripwire.
- Streamlit smoke: headless `streamlit run app.py` boots and serves
  `/_stcore/health` → 200 ok; the metrics-page module import is
  exercised by the focused test suite.
- Corpus audit: 287 files, 280 usable, 0 duplicates, 7 too-few-rows,
  1 non-numeric (sample.csv), 0 OHLC violations — read-only, no
  production state touched (file-set snapshots in the focused tests).

## 18. Sprint 12.3 — Warm pipeline profiling & cache effectiveness

Sprint 12.3 answers the question the cold-path profile (Sprint 12.2)
raised: *when the scanner cache is warm, where does the remaining
runtime go?*  It is an investigation-first sprint — every production
change had to clear the *measure → profile → compare cold vs warm →
identify → decide → optimise only if justified → re-measure* pipeline.
One production change was implemented (the scanner-cache LRU bound,
§18.7); everything else stayed untouched because the measured evidence
did not justify it.

### 18.1 Warm benchmark methodology (Sprint 12.3)

- **Environment**: Windows 10, Python 3.12.8, Streamlit 1.60.0.
- **Corpus**: deterministic synthetic (`write_csvs`, seeded, 500
  rows/symbol) at 50/200/500 symbols, plus the **real** corpus — a
  seeded subset of the shipped `data/raw` CSVs selected from the
  Sprint 12.2 audit's *usable* set (schema + rows + OHLC sane,
  duplicates removed, `sample.csv` excluded because the scanner itself
  filters it).  Real and synthetic results are labelled and never mixed.
- **Repetitions**: 3 cold + 5 warm per size (warm-500 measured ≈ 0.3 s
  per rep, so 5 reps stay cheap).
- **Cache state**: cold = scanner + indicator caches cleared before
  every repetition; warm = caches left populated between repetitions.
- **Counters** (observable, not inferred from wall time):
  - CSV reads — direct counter on `csv_loader._read_csv_robust`
  - scanner cache — `scanner_cache.stats()` per-tier hit/miss diffs
  - indicator calcs — `indicator_cache.stats()` miss diffs (each miss =
    one full rolling/ewm chain)
  - alert I/O — read/write counters on `load_history`/`save_history`
    (both `engine` + `history` namespaces), per-phase diffs
  - failures — `skipped` entries from the final scan
- **Isolation**: every run under `benchmarks.pipeline._state_files`;
  the Sprint 12.3 review also made `profile_cold_scan` /
  `profile_warm_scan` **self-isolating** (internal `_state_files` +
  temp dir) so even direct calls (tests, scripts) can never write
  synthetic symbols into the production `data/alerts/history.json`.
- Artifacts: `benchmarks/results/warm/warm_summary.json` +
  `benchmarks/results/profile_warm/` (pstats dumps, text reports,
  family tables).  Re-run with
  `python -m benchmarks.warm_path --sizes 50 200 500` and
  `python -m benchmarks.profile_warm_path --sizes 50 200 500`.

### 18.2 Cold vs warm results (measured, after the §18.7 fix)

| corpus | n | cold p50 (ms) | warm p50 (ms) | warm p95 | warm p99 | /sym warm (ms) | speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| synthetic | 50 | 3585.6 | 28.2 | 28.5 | 28.5 | 0.565 | 127.0x |
| synthetic | 200 | 11126.0 | 154.6 | 288.4 | 288.4 | 0.773 | 72.0x |
| synthetic | 500 | 29955.2 | 293.3 | 333.5 | 333.5 | 0.587 | 102.1x |
| real | 50 | 2938.1 | 27.8 | 30.3 | 30.3 | 0.556 | 105.7x |
| real | 200 | 11066.5 | 122.5 | 125.2 | 125.2 | 0.612 | 90.4x |

| counter (per phase totals) | cold (all sizes) | warm (all sizes) |
| --- | ---: | ---: |
| CSV reads | n × reps (150/600/1500) | **0** |
| scanner analysis hits | 0 | n × reps (250/1000/2500) |
| scanner analysis misses | n × reps | **0** |
| scanner df hits | 0 | **0** |
| indicator cache consulted | yes (n × reps misses) | **no** |
| alert reads / writes | 3 / 3 (one batch read+write per scan) | **0 / 0** |
| failures | 0 | 0 |

Key result: a warm scan performs **zero CSV reads, zero analysis
misses, zero indicator calculations and zero alert I/O** — every symbol
is a scanner-analysis-cache hit, so the warm cost is only
fingerprinting + ranking.  Per-symbol warm cost is flat (~0.56–0.77 ms)
across 50→500 symbols → linear scaling confirmed on the warm path too.

### 18.3 Cache effectiveness

Every application-level cache on the scanner path, with observed hit
behaviour:

| cache | location | key | TTL | hit behaviour (warm) | miss behaviour | invalidation | scope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Scanner df tier | `src/cache/scanner_cache.py` | file fingerprint (path:mtime_ns:size) | `SCANNER_CACHE_TTL` 300 s | cached DataFrame copy returned; `load_csv` not reached | parse + clean + sort + cache | source change / TTL / `clear()` | process-global singleton |
| Scanner analysis tier | same | fingerprint + RSI/MACD config | 300 s | cached analysis dict returned; `analyze_stock` not reached | full analysis + alert batch + cache | source change / config change / TTL | process-global |
| Indicator cache | `src/indicators/cache.py` | content fingerprint (OHLCV + config + version) | process lifetime (LRU 200) | **not consulted on warm scans** (scanner answers first) | full rolling/ewm chain | content/config/version change | process-global |

- **Application cache vs OS cache**: the 0-CSV-read warm result is the
  *application* scanner cache answering (fingerprint hit → cached df),
  not OS page cache — the loader is never even called on the warm path,
  and the benchmark counts `_read_csv_robust` calls directly.
- **Warm phase indicator hit rate**: reported as `consulted: False`
  (the cache counters never move on a warm scan), which is distinct
  from a 0 % hit rate — there are no misses because there are no lookups.
- **Capacity finding**: with the pre-fix default
  (`SCANNER_CACHE_MAX_ENTRIES=200`) a 500-symbol warm scan *silently
  re-parsed 300 of 500 symbols every scan* (LRU eviction beyond 200
  entries) — warm-500 collapsed to 1.6x and 1500 warm CSV reads.  See
  §18.7 for the fix.

### 18.4 Warm cProfile

Workload: warm `scan_market(workers=1)`, 500 symbols x 500 rows,
0.778 s profiled (cProfile overhead inflates wall time; percentages are
relative and comparable).  Warm-phase counters during the profiled scan:
**500 analysis hits, 0 misses** — provably no indicator-chain work.

Top functions by cumulative time:

| function | module | calls | tottime (s) | cumtime (s) | % total |
| --- | --- | ---: | ---: | ---: | ---: |
| `_analyze_file` | `src/scanner/engine.py` | 500 | 0.002 | 0.627 | 80.6 % |
| `get_analysis` | `src/cache/scanner_cache.py` | 500 | 0.013 | 0.622 | 79.8 % |
| `_file_fingerprint` | `src/cache/scanner_cache.py` | 500 | 0.015 | 0.604 | 77.6 % |
| `resolve` | `pathlib` | 500 | 0.005 | 0.476 | 61.1 % |
| `realpath` | `<frozen ntpath>` | 500 | 0.016 | 0.356 | 45.7 % |
| `nt._getfinalpathname` | `~` (Win32 syscall) | 1000 | 0.267 | 0.267 | 34.4 % |
| `stat` | `pathlib` | 1001 | 0.002 | 0.199 | 25.6 % |
| `nt.stat` | `~` | 1001 | 0.127 | 0.197 | 25.3 % |
| `__fspath__`/`__str__` | `pathlib` | 1502/3002 | ~0.02 | ~0.22 | ~14 % |
| `rank_market` | `src/scanner/ranking.py` | 1 | 0.013 | 0.057 | 7.3 % |

**Identified bottleneck:** the warm path is dominated by **per-access
file fingerprinting** — `_file_fingerprint` calls
`Path(path).resolve()` (→ `realpath` → the Win32 `GetFinalPathNameByHandle`
syscall, 34.4 % tottime) plus `stat` (25.3 %) for every cache access.
This is *application-level* code calling a filesystem syscall on every
lookup, though the absolute cost is small (~0.4 ms/symbol warm; ~200 ms
for a full 500-symbol warm scan).  `rank_market` (7.3 %) is the second
cost and is already efficient application code.

### 18.5 Indicator contribution (cold vs warm)

`profile_indicator_families` attributes one cold `analyze_dataframe`
(500 rows, profiled total 61.5 ms) by family entry point; the same
frame re-analysed is a **100 % indicator-cache hit**:

| family | cold cumtime (s) | % of cold total | warm contribution | cache behaviour |
| --- | ---: | ---: | ---: | --- |
| volume | 0.0141 | 22.9 % | ~0 | never reached warm |
| momentum (RSI/MACD) | 0.0127 | 20.6 % | ~0 | never reached warm |
| volatility (BB/ATR) | 0.0125 | 20.3 % | ~0 | never reached warm |
| moving averages | 0.0098 | 15.9 % | ~0 | never reached warm |

On the warm path all four families contribute ~0 by construction: the
scanner analysis cache answers before `analyze_dataframe` is called
(the indicator cache is not even consulted — §18.3).  The families'
cold shares (~16–23 % each) are only relevant to the cold path, which
Sprint 12.2 already documented as efficient library code.

### 18.6 DataFrame overhead on the warm path

Copy audit (`benchmarks.warm_path.audit_dataframe_copies` — an AST
parse, so only real ``.copy(...)`` *call* nodes count; docstring prose
mentioning ``.copy()`` cannot leak in): `analyzer.py` 1 (the single
defensive copy), `csv_loader.py` 1 (cache-miss copy-on-return),
`scanner_cache.py` 1 (`get_dataframe` copy-on-return),
`indicators/cache.py` 2 (`get` copy-on-return line 136, `put`
copy-on-store line 143), `backtest/engine.py` 1 (expanding-window
copy).

Measured warm-path contribution: **zero**.  On a warm scan the df tier
is never read (analysis hits skip `load_csv` entirely — 0 df hits in
§18.2), so no DataFrame copies, no column insertion, no pandas block
management occur on the warm path.  The ~14 % pandas `__setitem__`
overhead Sprint 12.2 found is cold-path-only.  The remaining copies
exist for mutation safety (copy-on-return / copy-on-put — Sprint 11.1
cache correctness) and were not removed: the measurement shows they do
not run on the warm path, and removing the cold-path defensive copy
would trade guaranteed caller isolation for a cost that is only on the
cold path.

### 18.7 Optimization decision (Sprint 12.3)

**Optimization implemented: YES — one change, capacity not algorithm.**

| | |
| --- | --- |
| Before | `SCANNER_CACHE_MAX_ENTRIES = 200` (per tier).  A 500-symbol warm scan silently evicted 300 symbols per LRU turn → warm-500 = 18.5 s, 1.6x speedup, 1500 warm CSV reads, 1500 analysis misses (5 reps).  The real corpus (280 usable) also exceeded the bound. |
| Evidence | §18.2 before-fix baseline: warm-500 re-parses 60 % of symbols every scan; per-size real-corpus runs degrade at the 200-symbol edge (10 warm CSV reads, 25 misses at real-200). |
| Change | Default raised `200 → 600` in `src/config/__init__.py` (env-overridable; ~24 KB per cached 500-row df-tier entry → ~15 MB at 600, acceptable for the scanner workload). |
| After | warm-500 = 293 ms, **102.1x** (127x at 50, 72x at 200); real-200 = 122 ms, 90.4x.  Warm CSV reads = 0, analysis misses = 0, indicator not consulted at every size. |
| Improvement | Warm 500-symbol scan: 18.5 s → 0.29 s (~64x absolute, 1.6x → 102x relative). |
| Risk | Low — the LRU bound is a memory cap, not correctness: values, hit semantics and invalidation are unchanged; the full suite and the `/api/analyze` p99 gate pass unchanged. |

Classification of every other finding:

- **A — already efficient**: warm path per-symbol cost is flat and tiny
  (~0.6 ms); the indicator chain, CSV loader, alerts and ranking need no
  change; per-family indicator costs are cold-path-only library code.
- **B — measurable, deferred**: `_file_fingerprint`'s `resolve()`
  syscall (~34 % tottime of the warm path).  A resolved-path memo would
  remove it, but the absolute saving is ~0.2-0.4 ms/symbol warm and the
  cache-key code is correctness-critical (fingerprint = invalidation) —
  deferred, not worth the churn in an investigation sprint.
- **C — implemented**: the LRU-capacity default (above).
- **D — correctness/safety**: one genuine issue found and fixed —
  `profile_cold_scan`/`profile_warm_scan` previously only isolated when
  called via the orchestrators; direct calls wrote synthetic symbols
  into the production `data/alerts/history.json`.  Both functions are
  now self-isolating (§18.1).

No other production code was changed: no Redis, no new cache layer, no
indicator rewrites, no DataFrame-copy removal, no WebSocket, no corpus
fabrication.

### 18.8 Remaining bottlenecks (measured only)

1. **Warm-path fingerprinting** — `resolve()`/`realpath`/`stat` per
   cache access (~60-78 % cumtime of a warm scan; ~0.4 ms/symbol).  An
   application-level memo of the resolved path is the concrete next
   optimization candidate (Category B).
2. **`rank_market`** — 7.3 % of the warm scan; already efficient
   application code (single pass + stable sort), no change warranted.
3. **Cold path unchanged** — CSV I/O + rolling/ewm remain the cold
   bottlenecks (Sprint 12.2 §17.6); the scanner cache now fully covers
   the real corpus, so warm scans no longer degrade into cold work.

### 18.9 Validation (Sprint 12.3)

- Focused: `tests/test_sprint12_3.py` — 18 passed (warm benchmark
  structure + counters, cache hit/miss/invalidation, worker scope,
  capacity-covers-real-corpus, full-real-corpus warm = all hits,
  warm profiler all-hits, indicator families discovered, copy audit
  incl. AST call-node regression + exact-count pins, state isolation
  on every path).
- Regression: Sprint 12.2 tests (`test_sprint12_2.py`,
  `test_performance.py`) — 42 passed.- Full suite: **2145 passed, 1 skipped, 0 failed** (Sprint 12.2:
  2127 passed — Sprint 12.3 adds 18 focused tests on top).
- CI gate (`benchmarks.ci_gate --factor 3.0`): **PASS** — all six
  checks green (cold_load, warm_speedup, warm_scan, warm_api,
  warm_portfolio, warm_api_p99), warm /api/analyze p99 well under the
  2000 ms tripwire.
- State isolation: PASS — production `data/alerts` file-set snapshots
  identical before/after every benchmark, profiler and test run.
- Streamlit: no UI code changed this sprint (the metrics-page TTL
  cache from Sprint 12.2 is untouched); the headless `streamlit run
  app.py` smoke result from §17.7 remains the current UI baseline.
