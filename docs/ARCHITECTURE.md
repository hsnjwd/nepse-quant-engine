# NEPSE Quant Engine — Architecture (v1.0)

## Sprint 13.8 — Platform Soak Testing & Performance Gate Hardening

Sprint 13.8 proves the engine remains *correct, bounded, recoverable and
measurable* across sustained operation, and eliminates the last known
benchmark flake (the Sprint 13.2 cold/warm speedup gate) by root-causing it
in the *harness*, not the engine.

### Performance-gate determinism (no threshold changes)

Two harness defects caused the cold/warm speedup flake; both are fixed in
`benchmarks/ci_gate.py` and `src/cache/scanner_cache.py`:

1. **Per-file filesystem syscall in the hot path.**
   `ScannerCache._file_fingerprint` called `Path(path).resolve()` once per
   file per rep — a pure fixed overhead with no change-detection value
   (fingerprints already combine content hashes + stat metadata).  Under
   disk/AV interference each call inflated by ~2 ms, and because the cost is
   *fixed per file*, it hurt the light warm leg (~1 ms/file baseline)
   disproportionately while barely moving the heavy cold leg — the exact
   asymmetry observed in failed artifacts (cold +27 ms / warm +27 ms on the
   same 12-file corpus).  Removed; fingerprints now derive from content +
   stat data already read.
2. **Sequential cold-then-warm measurement windows.**  The old protocol ran
   5 consecutive cold reps then 5 consecutive warm reps.  A machine slow
   phase (thermal, AV scan, background job) that started between the two
   windows inflated only one leg, corrupting the ratio even when the median
   of each leg was otherwise stable.  Legs now measure **interleaved
   cold/warm pairs**: pair *i* times a cold rep then a warm rep back-to-back
   under the same machine conditions, and the reported ratio is the median
   of the per-pair ratios.

### Environmental contention detection (single justified rerun)

The gate measures two signals during the pass:

- **wall/CPU ratio** — wall-clock vs `process_cpu_times()`; a ratio ≥ 1.5
  means the process was starved of CPU.
- **I/O probe** — a bounded alert-history read+write (the dominant fixed
  per-call cost in the light warm legs), sampled min-of-3 at pre/mid/post
  leg boundaries *and between every cold/warm pair* (so a burst overlapping
  the measured window is captured, not just bursts at the edges).  Max
  across points vs a 45 ms threshold.

A failing pass reruns **once** only when a probe exceeds its threshold, and
the rerun applies the identical acceptance requirements (speedup ≥ 5x, warm
API ≤ 0.75, warm portfolio ≤ 0.75, analyze p99 ≤ 2000 ms).  This is not
"retry until lucky": a rerun requires a *measured* environmental signal, and
the gate must pass on the retry.  Quiet single-sample I/O readings spike to
~80 ms on this box (AV jitter), so each probe point uses min-of-3 — quiet
max across 15 points is 29.5 ms, giving clean separation from the 45 ms
threshold.  The probe adds no measurable perturbation to quiet passes (no
spurious reruns in 13/13 verification runs).

### Soak framework (`benchmarks/soak.py`)

A bounded, hermetic, offline soak that repeatedly exercises the real
in-process pipeline the CI gate uses (`load_csv → analyze_stock →
scan_market`), never a simulation.  Five scenarios:

- **A — normal operation:** `get_history → analyze → scan → metrics` loop
  with a warm cache (the sustained-operation condition).
- **B — fallback:** primary healthy → primary unavailable → fallback →
  recovery, verifying provenance, provider health, fallback counters and
  cache behaviour across transitions.
- **C — conflict:** repeated AGREE / MATERIAL / AGREE / MAPPING_CONFLICT /
  AGREE injections; no state contamination.
- **D — cache:** cold / warm / invalidate / cold / warm cycles; bounded size
  and correct provenance.
- **E — worker:** start → request → stop → restart → request (opt-in, spawns
  a real uvicorn subprocess).

Each run reports per-scenario verdicts, bounded-state deltas (incident ring,
repeated-minor symbol set, quality trends, notifications, scanner cache),
resource deltas (threads, open file objects, temp/lock/corrupt litter) and a
tracemalloc memory envelope (plateau median vs end, 8 MiB cap).  Duration is
configurable (`SOAK_ITERATIONS` / `--iterations`, default 25) so CI stays
practical while deep runs remain possible.

**Hermeticity:** the soak resets exactly the process-global bounded state it
measures at entry (so suite residue from earlier tests cannot fail absolute
bound checks) and clears the process-wide scanner/indicator caches at exit.
Temp workspaces are always removed, on success and failure paths.

### Other reliability fixes

- **Windows session crash** — pytest crashed at session end cleaning the
  broken system `pytest-current` junction; tests now use a project-local
  `basetemp` (`.pytest_tmp/`, gitignored) via `tests/conftest.py`.
- **Worker aggregation race** — `test_sprint13_2.py` now settles the 5 s
  worker-metrics report throttle before asserting (harness fix only).
- **Sprint 12 ratio test** — bumped to the gate's 5-rep protocol (median-of-2
  had no outlier resistance); assertion unchanged.

## Sprint 13.6 — Production Readiness, Provider Diversity & Operational Observability

### Second-provider adapter (behind the existing interface)

Sprint 13.6 adds a *genuinely independent* second-provider adapter
(`GitHubCSVProvider`, `src/data/providers.py`) behind the existing
`BaseProvider` interface.  It is disabled by default — it activates only when
an operator sets `SECOND_PROVIDER_URL`; with an empty URL the provider chain
is byte-identical to pre-13.6 behaviour.

**Live two-provider reconciliation is NOT verified** — no second independent
provider has been validated in this environment.  The adapter contract and the
controlled-provider harness prove the architecture is provider-ready (a
second provider plugs in with no code change), but live reconciliation is
explicitly documented as `NOT AVAILABLE` until an operator validates a second
source.

### Provider reliability history & degradation

`src/data/health.py` extends `ProviderHealthMonitor` with:

- bounded per-provider outcome window (`outcome_window`, default 100)
- `record_success` / `record_timeout` / `record_malformed` /
  `record_failure` / `record_empty` (empty responses are tracked separately
  and never count toward the disable threshold)
- degradation classification `HEALTHY / DEGRADED / UNAVAILABLE / UNKNOWN`
  from configurable triggers (failure ratio, timeout/malformed/empty counts,
  minimum window samples)
- automatic recovery: after `success_recovery_count` consecutive successes the
  provider re-enables; `record_success` clears the consecutive-failure latch
- compact `reliability_history()` snapshot per provider

### Reconciliation incidents (bounded)

`src/data/incidents.py` — `IncidentTracker`:

- bounded event ring (`max_events`, default 200) with compact records
  (timestamp, kind, providers, symbol, trust consequence, fallback/quarantine
  flags) — never raw market data
- per-kind scalar counters exposed via `/metrics`
- repeated-minor detection (threshold `REPEATED_MINOR_THRESHOLD`, bounded
  symbol set `MAX_TRACKED_SYMBOLS`)

Incident kinds: material disagreement, mapping conflict, identity mismatch,
provider timeout, provider unavailable, fallback, malformed response.

### Data-quality trends (bounded)

`src/data/quality.py` — `QualityTrendTracker` records the latest two corpus
aggregates (valid/invalid/stale/suspicious/quarantined/missing-session/
calendar-invalid counts) and exposes `trend()` (previous + current + deltas)
so operators can answer *"is data quality getting better or worse?"*.

### Calendar governance workflow

`src/data/calendar.py`:

- `validate_candidate_calendar(data, current)` — schema + semantic + coverage +
  provenance validation plus an overlap-compatibility gate (a candidate whose
  window overlaps the active calendar must keep the same trading week) and a
  weekend-regression gate (known NEPSE Fri/Sat must stay non-trading)
- `update_calendar(...)` — validate → backup previous → atomic write →
  version-history record; a rejected candidate never replaces the active
  calendar
- `rollback_calendar(...)` — restores the most recent valid backup, validated
  on its own merits (never blocked by an incompatible current calendar);
  reversible (the pre-rollback calendar is itself backed up)

### Operational status (derived, no new subsystem)

`src/data/operational_status.py` — `system_status()` derives a compact
`HEALTHY / DEGRADED / UNAVAILABLE` status from existing provider health,
reconciliation, calendar, quality and cache state.  No duplicate monitoring
infrastructure.

### /metrics additions (bounded, backward compatible)

`src/api/metrics.py` gains bounded blocks: `provider_health` (with reliability
history + degradation), `reconciliation.incidents`, `data_quality.trend`,
`calendar.updates` (version history), and `system_status`.  All blocks are
safe-when-empty, JSON-serialisable and bounded.

### Cache restart/recovery

Verified: disk-tier provenance survives restart; conflicted entries are never
cached (and a conflicted entry that somehow exists fails safe); fallback
provenance survives restart; corrupt cache files fail safely (isolated to
their key).

## Sprint 13.5 — Data Trust, Calendar Governance & Reliability

### Data trust lifecycle

Every market dataset flows through a single auditable trust pipeline.  At each
gate a compact, bounded provenance record is attached (or the data is rejected)
so a consumer can always answer: *where did this come from, was it reconciled,
was it calendar-valid, is it trusted, did fallback occur, can it be cached, and
why was the resulting signal allowed or suppressed?*

```
Provider
  │  (identity validation — check_symbol_mapping, never compare different
  │   securities)  +  quality validation — validate_history_frame rejects
  │   malformed/empty/invalid payloads before they enter the chain)
  ▼
Reconciliation (src/data/reconciliation.py)
  │  AGREE / MINOR → trusted; MATERIAL / MAPPING → quarantined, never a
  │  trusted record; UNAVAILABLE → fallback (never averaged)
  ▼
Calendar validation (src/data/calendar.py + data/state/nepse_calendar.json)
  │  governed versioned calendar: weekend rule Sun–Thu, explicit holidays /
  │  special sessions / exceptional closures, provenance, effective window.
  │  Rows on governed non-trading days → R_NON_TRADING_DATE quality failure.
  │  Malformed calendar → fail-safe load rejects it, base calendar used.
  ▼
Quality validation (src/data/quality.py)
  │  canonical OHLCV contract; INVALID quarantines the symbol
  ▼
Provenance (src/data/provenance.py)
  │  trust states: trusted / reconciled / single_provider / fallback /
  │  partially_reconciled / conflicted / unavailable / stale /
  │  calendar_invalid / quarantined (worst-state-wins resolver)
  ▼
Cache (src/data/service.py + src/data/cache.py)
  │  reconciled entries embed their state; conflicted outcomes are NEVER
  │  cached as trusted; fallback data carries fallback_used provenance;
  │  targeted invalidation via delete_prefix / clear_reconciled_cache
  ▼
Analyzer (src/engine/analyzer.py)
  │  additive data_quality / reconciliation / provenance blocks
  ▼
Signal safety
     unsafe trust (conflicted / unavailable / calendar-invalid / quarantined)
     → signal forced to HOLD (signal_suppressed=True, reason = trust state)
```

### Calendar governance

- **Versioned data file** — `data/state/nepse_calendar.json` (schema `2.0`):
  `calendar_version`, `effective_from/to`, `timezone`, `trading_week`,
  `holidays`, `special_sessions`, `closures`, `provenance`, `last_validated`,
  `source`, `operator_notes`.  Holidays are **not** hard-coded in Python.
- **Fail-safe load** — `NepseCalendar.load` validates the file
  (`validate_calendar_data`) and rejects malformed/contradictory/unsupported
  calendars with logged diagnostics, falling back to the base weekend-rule
  calendar.  A broken calendar can never silently turn an exchange closure into
  a normal trading day.
- **Provenance** — `to_status()` / `coverage_for()` expose version / window /
  last-validated / source / counts (never raw date lists), safe for `/metrics`.
- **Known limitation** — the shipped baseline documents only the Sun–Thu weekend
  rule (long-standing NEPSE schedule) with honest provenance and **zero
  fabricated holidays**.  Holiday/special-session/closure lists are
  operator-maintained: add dated entries with a name and source, bump
  `calendar_version`, update `last_validated`.  No external/authoritative
  calendar provenance is claimed.

### Provider reliability & reconciliation observability

- `/metrics` gains bounded `provider_health` (per-provider enabled, success-rate,
  requests, failures, consecutive failures, last-success age, latency — from the
  existing `ProviderHealthMonitor`; no second health architecture) and bounded
  `calendar` status.  `reconciliation` / `data_quality` scalar counters remain.
- `HybridProvider.fallback_used` reflects the *current* request (reset per call)
  so a recovered primary provider is never mislabelled as fallback.
- **Known limitation** — production currently configures **one** live provider
  (nepse_scraper / yonepse).  The reconciliation pipeline is provider-agnostic:
  a second provider exposing the hybrid `get_reconciled_history` contract plugs
  into `HybridProvider` + `DataService` with no architectural change (verified
  with a controlled provider double).

### Cache trust semantics

| State | Cached? | Provenance preserved? |
|---|---|---|
| AGREE / MINOR | yes | yes (reconciled / partially_reconciled) |
| MATERIAL / MAPPING | **never** | returned fresh, trust=conflicted, unsafe |
| fallback-served | yes | yes (fallback_used=True — never primary) |
| unavailable | never cached | trust=unavailable, unsafe |
| calendar-invalid | never as trusted | trust=calendar_invalid (via quality), unsafe |

Targeted invalidation: `clear_reconciled_cache(symbol=None, days=None)` drops
only the `reconciled:*` namespace (exact-key delete for symbol+horizon, prefix
delete otherwise) — plain history, quotes and summaries stay warm.

## Sprint 13.4 — Cross-Provider Reconciliation & Trading Calendar Integrity

### Reconciliation architecture

The engine moved from *provider priority wins* to *reconcile before trust*:

```
Provider A ─────┐
                ├── Normalize ──► Reconcile ──► trusted result
Provider B ─────┘        (src/data/reconciliation.py)
       ┌──────────────┴───────────────┐
       ↓                              ↓
  AGREE / MINOR                   MATERIAL / UNAVAILABLE /
       ↓                              MAPPING_CONFLICT
  validated data                       no unsafe signal
```

Statuses (Phase 5):

| Status | Meaning | Action |
|---|---|---|
| `AGREE` | providers agree within tolerance | validated data used |
| `MINOR_DISAGREEMENT` | drift within material threshold | documented preferred source + warning (**never averaged**) |
| `MATERIAL_DISAGREEMENT` | drift exceeds material threshold | conflicting dates quarantined; never a trusted record |
| `UNAVAILABLE` | no provider data | fallback chain |
| `MAPPING_CONFLICT` | providers disagree about the security | never compared; no signal |

### Provider comparison rules (Phase 6)

- Fields reconciled at minimum: `open` / `high` / `low` / `close` / `volume`;
  optional fields (`previous_close`, `turnover`, `transactions`) reconciled
  only when both providers supply them — never required.
- Relative difference: `abs(a-b) / max(abs(a), abs(b))`.
- Per-field tolerances (configurable): price `RECONCILE_PRICE_TOLERANCE_PCT`
  (1%), volume `RECONCILE_VOLUME_TOLERANCE_PCT` (10%).  Material thresholds:
  price 5%, volume 50% (`RECONCILE_MATERIAL_PRICE_PCT` /
  `RECONCILE_MATERIAL_VOLUME_PCT`).  Never one fixed percentage for every field.
- Preferred source `RECONCILE_PREFERRED_SOURCE=api` resolves MINOR
  disagreements (with a warning attached); `reconcile_history` drops
  materially-conflicting dates instead of averaging them.

### Conflict states & signal behaviour (Phase 7/17)

- `AGREE` → normal analysis.
- `MINOR` → warning/provenance; signal not suppressed.
- `MATERIAL` / `MAPPING_CONFLICT` → signal forced to HOLD with
  `signal_suppression_reason=material_disagreement|mapping_conflict`;
  suppression is applied *last* in `analyze_dataframe` so a provider
  conflict wins even on an otherwise VALID frame.
- `UNAVAILABLE` → caller falls back; no unsafe signal.

### Provenance (Phase 8)

`ReconciliationResult` exposes `status`, `symbol`, `date`, `providers`,
`selected_source`, `disagreement_fields`, `difference_metrics`,
`warnings`, `as_of`, `fallback_used` — never raw provider payloads.
`DataService.get_reconciled_history` returns the trusted frame **with
its provenance attached**, and caches it under a dedicated namespace so
a previously-conflicted record can never silently become trusted data.

### Symbol mapping (Phase 15)

`check_symbol_mapping` verifies provider identifiers *and* company
identity resolve to the same security before any value comparison — a
mismatched identifier or company-name disagreement yields
`MAPPING_CONFLICT` and the records are never reconciled.

### Corporate-action awareness (Phase 14)

`classify_price_jump` flags day-over-day moves above
`RECONCILE_CORPORATE_ACTION_MOVE_PCT` (20%) with sound OHLC structure as
`large_but_legitimate` (dividend / right / bonus / split) — annotated,
never quarantined.  Structurally-impossible OHLC is rejected regardless
of size.

### Trading calendar (Phase 10-13)

`src/data/calendar.py` centralizes NEPSE session semantics with explicit
provenance:

- **Weekend rule** — NEPSE trades **Sunday–Thursday**; Friday and Saturday
  are the weekly closure (documented NEPSE schedule, not invented).
- **Holidays** — operator-maintained, versioned list; no holiday is
  hard-coded without provenance.
- **Observed sessions** — the union of validated historical sessions
  derived from the corpus (`NepseCalendar.from_corpus`) — the strongest
  available evidence.

Day classification: `TRADING_DAY / HOLIDAY / WEEKEND / UNKNOWN`.
Gap classification: `EXPECTED_NON_TRADING_DAY` (weekend/holiday/
corpus-verified closure) vs `MISSING_TRADING_SESSION` (scheduled session
with no record, corpus confirms the market traded) vs `UNKNOWN` (no
evidence — explicit uncertainty, never a guess).  Missing records are
never fabricated.

`assess_history(..., calendar=...)` uses these semantics for continuity:
a Friday/Saturday closure is not missing data; a genuinely-missing
scheduled session increments `report.missing_sessions`.  The legacy
business-day heuristic remains the default for existing callers.

### HybridProvider integration (Phase 16)

`HybridProvider.get_reconciled_history` is **additive**: it queries every
healthy provider, validates each payload, reconciles the results and
returns `(merged_frame, ReconciliationResult)`.  The existing fail-fast
`get_history` fallback chain, CSV fallback, timeout handling and
DataService caching are untouched.

### Metrics (Phase 9/20)

`GET /metrics` gained a bounded `reconciliation` block (provider
requests/successes/failures/timeouts, fallback_count, reconciliation
checks, agreements, minor/material disagreements, mapping conflicts,
quarantined conflicts).  Scalar-only; all existing keys preserved.

## Sprint 13.3 — Live Data Quality & Provider Reliability

### Data-quality pipeline

The engine never lets raw provider data flow straight into indicators,
signals or ranking.  Every OHLCV payload passes the canonical contract
before it becomes a signal:

```
Raw Provider Data
       ↓
Provider Normalization   (BaseProvider / CSVProvider / HybridProvider)
       ↓
Data Quality Validation  (src/data/quality.py — VALID/INVALID/SUSPICIOUS)
       ↓
Validated Market Data    (DataService; InvalidDataError → next provider)
       ↓
Indicators → Signals     (INVALID suppresses signal to HOLD; STALE flagged)
       ↓
Ranking / Scanner        (INVALID files → skipped, never ranked)
```

### Canonical market-data contract

| Field | Requirement |
|---|---|
| `symbol` | non-empty, uppercase-normalized; unknown/delisted → `None` (never another company's data) |
| `date` | parseable, not in the future; duplicates deduplicated if identical, flagged `CONFLICT` if disagreeing |
| `open/high/low/close` | finite, `> 0`, `high >= max(open, close)`, `low <= min(open, close)`, `high >= low` |
| `volume` | finite, `>= 0` |
| `previous_close/change/change_percent/turnover/transactions` | optional — absence never fails a record |

### Validation states

- `VALID` — no issues.
- `SUSPICIOUS` — warnings only (identical duplicates, business-day gaps
  > `DATA_MAX_GAP_DAYS`, stale data).
- `INVALID` — at least one error (broken OHLC, NaN/inf, future/unparseable
  date, conflicting duplicates, missing columns).

Freshness: `FRESH` (age ≤ `DATA_STALE_AFTER_DAYS`), `STALE` (> threshold),
`UNKNOWN` (no date).  Thresholds are config: `DATA_STALE_AFTER_DAYS=7`,
`DATA_MAX_GAP_DAYS=14`, `ENFORCE_SIGNAL_FRESHNESS=false`.

### Provider hierarchy & fallback rules

```
HybridProvider (priority order)
   ├─ APIProvider  ── timeout → ProviderTimeout (distinguishable)
   │                  connection/HTTP failure → ProviderError
   │                  malformed OHLC → InvalidDataError (validator hook)
   ├─ CSVProvider  ── local corpus fallback (stem-deduplicated discovery)
   └─ all fail     → DataService returns controlled empty / unavailable
```

Rules: prefer fallback over failure when the fallback is validated; never
silently average conflicting provider prices (`detect_disagreement` flags
material >1% differences); never fabricate missing candles; keep the last
known valid value but label it stale.

### Signal safety

- `VALID` → normal signal.
- `STALE` → flagged in `data_quality`; suppressed to HOLD only when
  `ENFORCE_SIGNAL_FRESHNESS=true`.
- `INVALID` / `CONFLICT` → signal suppressed to HOLD with
  `signal_suppression_reason="invalid_data"`.
- `UNAVAILABLE` → no signal; market status reports `available=false`
  (a provider failure is never rendered as `NEPSE 0.00`).

Every analysis and the market-status payload carry an **additive**
`data_quality` block (backward-compatible):

```json
"data_quality": {
  "status": "VALID", "freshness": "FRESH",
  "records_checked": 200, "records_valid": 200, "records_invalid": 0,
  "duplicates": 0, "conflicts": 0,
  "latest_date": "2026-08-12", "as_of": "2026-08-12",
  "age_days": 1, "symbol": "NABIL", "source": "csv",
  "reasons": []
}
```

### Market-status semantics

`/market/status` and `/market-status` both delegate to
`market_status_response()` (DataService-backed).  Legacy keys
(index/change/change_pct/volume/turnover/advances/declines/unchanged/
status/is_open/timestamp) are preserved; `data_quality.available` is
authoritative — an `Unknown`-status (empty) summary is *unavailable*, not a
real zero, and its zeroed core fields are listed in `unknown_fields`.
Cached snapshots are labelled by age (`age_seconds`/`freshness`) so a
stale cache is never presented as a fresh live print.  Provider failure
exposes a sanitized 500 (`Failed to fetch market status`) — never
internal URLs, paths or tracebacks.

### Data-quality metrics & commands

- Bounded thread-safe counters (`QualityMetricsCollector`) under
  `GET /metrics` → `data_quality` (records, duplicates, conflicts, stale,
  provider failures/timeouts, fallbacks).
- Report: `python -m src.data.quality --data-dir data/raw` — per-symbol
  table + aggregates; exits non-zero on INVALID/conflicts.

### What the engine does not guarantee

- No authoritative trading calendar — weekends/holidays are not rejected
  as invalid; gaps are business-day heuristic only.
- Stale-but-valid data is *flagged*, not suppressed, unless
  `ENFORCE_SIGNAL_FRESHNESS=true`.
- Cross-provider disagreement is *detected* (utility) but the hybrid
  chain prefers priority order rather than arbitrating two successful
  providers.
- The real corpus may contain bad rows (e.g. 65 symbols with a zero-price
  row on 2026-07-24); those symbols are quarantined from ranking until
  the underlying rows are repaired.

## Sprint 13.2 — Multi-Worker Reliability & Failure-Path Hardening

### Multi-worker deployment

```
uvicorn src.api.main:app --host 127.0.0.1 --port <PORT> --workers 2   (Docker api stage)
   │
   ├─ worker A (pid X) ── in-process tracker / caches / DataService singleton
   ├─ worker B (pid Y) ── in-process tracker / caches / DataService singleton
   │
   ├─ FastAPI lifespan logs  worker start/stop pid=… per worker (Phase 14)
   ├─ shared state: alerts/history.json, portfolio.json, watchlist.json,
   │     worker-metrics store — all via transactional atomic json_store
   │     (update_json: cross-process lock, atomic rename, corrupt recovery)
   └─ GET /metrics  ── any worker self-reports into the shared store and
        aggregates active workers:  workers.active / known, aggregate.hits /
        misses / lock stats / requests / request_errors (worker A + B = total)
```

- **Aggregation is sum-without-double-count**: each request is served by
exactly one worker, each worker persists only its own counters, and the
aggregate sums every *active* (TTL-fresh) record — so
``aggregate.requests == A.requests + B.requests`` holds by construction.
- **Worker restart / recovery**: a killed worker's record goes stale after
``WORKER_METRICS_TTL_S`` and stops being counted (never errors, never
blocks a fresh record under a new pid); uvicorn respawns the worker; the
fleet keeps serving (verified live).
- **Orphan protection**: `benchmarks.validate_workers._shutdown` always
runs ``taskkill /T /F`` on Windows after the graceful signal, because
multiprocessing-spawned uvicorn workers are not in the master's console
group and can survive its exit (observed orphans held state files open).

### Provider failure semantics

| Failure | Behaviour |
|---------|-----------|
| Provider timeout | ``ProviderTimeout`` (distinct subclass), service returns controlled empty history/summary, `provider_failures` metric increments |
| Connection error | Generic ``ProviderError``; hybrid falls back to next provider (CSV) |
| HTTP error | Generic ``ProviderError`` (404 weekend shards skipped, not counted as connection failures) |
| Malformed response | Generic ``ProviderError`` / skipped entry |
| Empty response | Safe empty/error result, never a crash |
| Invalid symbol | Validation/client error (400/404), no internal detail |
| Provider unavailable | Controlled failure; API stays up (CSV fallback / empty default) |

`APIProvider` honours the documented ``API_TIMEOUT`` config (was
hard-coded 15 s); timeouts are tracked by failure kind so the `_do_*`
boundary raises ``ProviderTimeout`` — distinguishable from generic
failures, and both remain ``ProviderError`` subclasses so existing
callers are unaffected.  500 error details are sanitized: no tracebacks,
filesystem paths, tokens or internal URLs reach clients (stable
prefixes preserved).

### CI gate at the process level

```
python -m benchmarks.ci_gate --factor 3.0 --out benchmark-ci.json
  PASS   → exit 0
  FAIL   → exit 1 (PERFORMANCE GATE FAILED block: actual / maximum / reason)
  INVALID → exit 1 (missing/malformed/NaN/inf/empty data fails explicitly)
```

``evaluate_checks`` is pure and fail-safe: every measurement is coerced
through ``_pos`` (strictly positive finite float) so a missing/zero/
non-finite value can never produce a plausible ``ratio 0.0 <= max``
PASS.  Threshold boundaries use the repository's ``<=`` semantics
(``0.750 == 0.750`` passes; ``0.751`` fails; ``2000.0 == 2000.0``
passes; ``2000.1`` fails) — pinned by tests.  The CI workflow runs the
gate and the exit code drives the job result; a regression test proves
the workflow invokes it.

## Sprint 13.1 — Production API & Dashboard Integration Hardening

### Production request path (single source of truth)

```
FastAPI endpoint (/analyze, /portfolio, /watchlist, /market/*, /metrics)
   │
   ├─ request_timing middleware ──► ApiRequestTracker (bounded, thread-safe)
   │        │                          per-endpoint latency + error windows
   │        ▼
   service / engine layer
   │        ├─ /analyze     → src.engine.analyzer.analyze_stock   (indicator cache)
   │        ├─ /portfolio   → src.portfolio.analyzer.analyze_portfolio
   │        ├─ /watchlist   → src.watchlist.manager / scanner
   │        ├─ /market/*    → src.services.market_service / DataService
   │        └─ /metrics     → DataService + scanner/indicator/json-store caches
   ▼
DataService (singleton) → provider chain (API → CSV) → cache tier
```

No API module imports ``APIProvider`` / ``CSVProvider`` / ``HybridProvider``
directly — endpoints route through DataService or the service layer
(verified by ``tests/test_sprint13_1.py::TestProductionPathVerification``).
The two backtest engines are both production: ``src/backtest/engine`` is
the ``/backtest`` + ``/simulation`` hot path, ``src/backtesting/`` is the
advanced event-driven engine — not legacy dead code.

### Metrics flow (API is the source of truth)

```
Source of truth:  GET /metrics (src/api/metrics.py)
     │  process / worker identity (pid, hostname)
     │  metrics       — DataService request counters, cache hit rate, p95/p99
     │  per_operation / per_provider
     │  api_requests  — bounded per-endpoint HTTP latency + errors (Sprint 13.1)
     │  performance_gates — CI thresholds + last recorded verdict (Sprint 13.1)
     │  indicator_cache / scanner_cache / json_store / memory / status
     │  workers + aggregate — cross-worker self-reports (Sprint 12.0)
     ▼
Dashboard (src/ui/pages/metrics_page.py) — pure fetch/summarize helpers,
     never crashes on missing/malformed/stale/zero payloads, 10s cache TTL
     ▼
CI (benchmarks/ci_gate.py) — enforces warm API/portfolio ratios + analyze p99
```

- **Instrumentation** — every HTTP request is timed by the middleware and
  recorded into ``ApiRequestTracker`` (``src/api/timing.py``): bounded
  per-endpoint latency windows (deque maxlen), thread-safe, never raises,
  health/root/metrics probes excluded so percentiles stay meaningful.
- **Performance gates are enforced, not informational** — CI runs
  ``python -m benchmarks.ci_gate`` (``.github/workflows/ci.yml``); the gate
  exits non-zero on failure and prints an actionable block
  (``PERFORMANCE GATE FAILED … actual / maximum / reason``).
- **Fail-safe evaluation** — ``ci_gate.evaluate_checks`` is pure and
  treats a missing / malformed / zero / non-finite measurement as an
  explicit FAIL (a corrupted run can no longer produce a plausible
  ``ratio 0.0 <= max`` PASS).  ``check_details`` / ``check_reasons`` /
  ``failures`` are written into the ``benchmark-ci.json`` artifact.
- **Dashboard contract** — the metrics page consumes exactly what the
  API exposes: API latency percentiles, scanner cache counters, and the
  performance-gate verdicts, without re-deriving gate arithmetic.

## Sprint 11 — Performance, Scalability & Observability

### Scanner architecture (parallel + cached)

```
scan_market()
   │  files = sorted(DATA_DIRECTORY/*.csv)   (sample.csv excluded)
   ▼
ThreadPoolExecutor(max_workers=SCANNER_WORKERS)
   │  executor.map(_analyze_file, files)   ← input order preserved
   ▼
_analyze_file(file)
   │  scanner_cache.get_analysis(file) ── hit? ──► analysis
   │          │ miss
   │          ▼
   │  analyze_stock(file)
   │     load_csv(file) ──► scanner_cache.get_dataframe ── hit? ──► df
   │          │ miss                                 │
   │          ▼                                     │
   │  _read_csv_robust → clean → put_dataframe       │
   │  analyze_dataframe(df)  (1 defensive copy + in-place indicators)
   │  process_alerts(...)   (RLock-protected, shared history.json)
   │  put_analysis(file, result)
   ▼
rank_market(results)   (stable sort → deterministic ranks)
```

- **Parallelism**: bounded thread pool sized by `SCANNER_WORKERS`;
  `executor.map` yields results in file order, so output is
deterministic regardless of scheduling.
- **Isolation**: every file is analysed in its own worker; a failure
  yields a `skipped` entry without aborting the scan.
- **Cache**: `src/cache/scanner_cache.py` stores parsed DataFrames and
  analysis outputs keyed by file fingerprint (path, mtime, size).
  Invalidation: source change / TTL (`SCANNER_CACHE_TTL`) / indicator
  config change (analysis keys embed RSI/MACD parameters).

### Indicator pipeline (single-copy)

`analyze_dataframe` now makes exactly **one** defensive `df.copy()` and
threads `inplace=True` through every `add_*` step (moving averages,
RSI/MACD, volume, volatility). Standalone calls keep the old
copy-per-call behaviour (`inplace=False` default). ATR uses element-wise
`np.fmax` instead of a temporary `pd.concat` frame — identical numerics,
no intermediate allocation.

### DataService history optimisation

- `get_history` TTL configurable via `HISTORY_CACHE_TTL`.
- `get_history_batch(symbols, days)` fetches only cache misses — used
  by the live-market scan fallback.

### Observability

- `GET /metrics` (FastAPI) — request metrics, cache hit ratio, provider
  stats, scanner-cache stats, memory.
- Request-timing middleware logs structured `timing name=api_request …`
  records; `src/logging/timing.py::timed` provides the same for engine
  code.

### Benchmark suite

`scripts/benchmarks.py` measures scan (cold/warm), analysis, history
(miss/hit/batch), backtest, API latency, startup and peak scan memory;
results are stored under `benchmarks/results/` and compared on re-run.

## Sprint 10 — Architecture Consolidation

Sprint 10 established **single sources of truth** across the platform:

| Concern | Single source of truth | Notes |
|---------|------------------------|-------|
| CSV parsing | `src/loaders/csv_loader.py::load_csv` | `CSVProvider._load_csv` delegates here; `analyzer`, `backtest.engine` and the API all route through it |
| CSV path resolution | `src/loaders/csv_loader.py::resolve_stock_csv_path` | Replaces inline `Path(DATA_DIRECTORY)/symbol.csv` logic in `api/analyze.py`, `api/backtest.py`, `watchlist/scanner.py` |
| Confidence engine | `src/decision/confidence.py::calculate_confidence` | Legacy `src/decision/engine.py` is deprecated (backward-compatible, warns) |
| Configuration | `src/config.py` | `DataService._default_api_urls` is derived from `DATA_SERVICE_API_URLS` (5 keys incl. `github_datasets`) |
| Version | `src/version.py` (reads root `VERSION` file) | Used by `src.api.main`, `src/__init__` |

### Canonical data flow

```
CSV/API
   │
   ▼
Providers (src/data/providers.py)     CSV parsing ONLY via src/loaders/csv_loader.load_csv
   │
   ▼
DataService (src/data/service.py)     single entry point, tiered cache, health monitor
   │
   ├─► Indicators (src/indicators/)    SMA_20/SMA_50, RSI, MACD, ATR, BB …
   │
   ├─► Strategies (src/strategies/)    MomentumStrategy reads SMA_20/SMA_50 (no silent fallback)
   │
   ├─► Signal Engine (src/signals/)    scoring → confidence (src/decision/confidence.py)
   │
   ├─► Decision Engine (src/decision/) single confidence implementation
   │
   ├─► Portfolio / Alerts / API / Telegram Bot / UI
```

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Streamlit Frontend                          │
│                       (app.py + 24 page modules)                    │
│                                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │Dashboard│ │ Scanner  │ │ Analysis │ │Portfolio│ │ Settings  │  │
│  └────┬────┘ └────┬─────┘ └────┬─────┘ └────┬───┘ └─────┬─────┘  │
│       │           │            │            │            │        │
│  ┌────┴───────────┴────────────┴────────────┴────────────┴─────┐  │
│  │               UI Components / Helpers / Theme                │  │
│  │    (src.ui.components, src.ui.helpers, src.ui.theme)         │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                     │
└──────────────────────────────┼─────────────────────────────────────┘
                               │
┌──────────────────────────────┼─────────────────────────────────────┐
│                  DataService (src.data.service)                     │
│                     (Singleton, Background Refresh)                  │
│                                                                     │
│  ┌──────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │Tiered    │ │  Hybrid      │ │ Metrics  │ │ LiveMarketStream  │  │
│  │Cache     │ │  Provider    │ │Collector │ │ (WebSocket)       │  │
│  └────┬─────┘ └──────┬───────┘ └──────────┘ └────────┬─────────┘  │
│       │              │                                │            │
│  ┌────┴─────┐   ┌────┴───────┐               ┌───────┴────────┐  │
│  │Memory    │   │ API       │               │  Auto-Reconnect │  │
│  │Cache     │   │ Provider  │               │  + Heartbeat    │  │
│  ├──────────┤   ├───────────┤               │  + Subscribers  │  │
│  │Disk      │   │ CSV       │               └─────────────────┘  │
│  │Cache     │   │ Provider  │                                     │
│  └──────────┘   └────┬───────┘                                     │
│                      │                                             │
│  ┌──────────┐   ┌────┴───────┐   ┌──────────┐   ┌──────────────┐  │
│  │  Rate    │   │  Health    │   │  Live    │   │  Exceptions  │  │
│  │ Limiter  │   │  Monitor   │   │  Data    │   │  Hierarchy   │  │
│  └──────────┘   └────────────┘   └──────────┘   └──────────────┘  │
│                                                                     │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────┼─────────────────────────────────────┐
│                      Backend Modules                               │
│                                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │ Engine  │ │ Portfolio│ │ Paper    │ │ Backtest│ │ Replay    │  │
│  │(analyzer)│ │ Database │ │ Trading  │ │ Engine  │ │ Engine    │  │
│  └────┬────┘ └──────────┘ └──────────┘ └────┬────┘ └─────┬─────┘  │
│       │                                     │            │        │
│  ┌────┴────┐  ┌──────────┐ ┌──────────┐ ┌──┴─────┐ ┌───┴──────┐  │
│  │Indicators│  │ Regime  │ │ Signals  │ │Scanner │ │ Trading  │  │
│  │(MA, RSI, │  │ Detector│ │ Scorer   │ │ Engine │ │ Journal  │  │
│  │ MACD...) │  └──────────┘ └──────────┘ └────────┘ └──────────┘  │
│  └──────────┘                                                      │
│                                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │Strategies│ │ Risk     │ │ Decision │ │ Alerts │ │ Watchlist  │  │
│  │(momentum,│ │ Engine   │ │ Engine   │ │ Center │ │ Manager    │  │
│  │breakout) │ └──────────┘ └──────────┘ └────────┘ └───────────┘  │
│  └──────────┘                                                      │
│                                                                     │
|  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │FastAPI   │ │ Monte    │ │ Parameter│ │ Walk   │ │ Export    │  │
│  │REST API  │ │ Carlo    │ │ Optimiser│ │ Forward│ │ Center    │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ └───────────┘  │
│                                                                     │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
               ┌────▼────┐          ┌────▼────┐
               │NEPSE API│          │Local CSV│
               │Sources  │          │ Data    │
               └─────────┘          └─────────┘
```

## Data Flow

```
User Action                     Streamlit Page
      │                              │
      ▼                              ▼
   UI Event ────────────────► DataService.get_*()
                                    │
                          ┌─────────┴──────────┐
                          ▼                    ▼
                    TieredCache           HybridProvider
                          │                    │
                    ┌─────┴─────┐        ┌─────┴──────┐
                    ▼           ▼        ▼            ▼
              Memory Cache  Disk Cache  API      CSV Provider
                                         │            │
                                    ┌────┴────┐  ┌────┴────┐
                                    │  Rate   │  │ Auto-   │
                                    │ Limiter │  │ Discover│
                                    └─────────┘  └─────────┘
                                         │
                                    ┌────┴────┐
                                    │  Health │
                                    │ Monitor │
                                    └─────────┘
```

## Module Dependency Graph

```
src.ui.pages.*
    ↓
src.ui.components / src.ui.helpers / src.ui.theme
    ↓
src.data.service.DataService
    ↓
├── src.data.cache.TieredCache
│   ├── MemoryCache
│   └── DiskCache
├── src.data.providers.HybridProvider
│   ├── APIProvider (→ requests/httpx → NEPSE API)
│   └── CSVProvider (→ pd.read_csv → local files)
├── src.data.websocket.LiveMarketStream
├── src.data.metrics.MetricsCollector
├── src.data.health.ProviderHealthMonitor
├── src.data.rate_limiter.RateLimiter
├── src.data.export.ExportCenter
│
├── src.portfolio.database.PortfolioDatabase
├── src.paper_trading.engine.PaperTradingEngine
├── src.replay.engine.MarketReplayEngine
├── src.alerts.center.AlertCenter
├── src.trading.journal.TradeJournal
│
├── src.engine.analyzer
├── src.regime.detector
├── src.signals.scorer
├── src.scanner.engine
├── src.strategies.*
├── src.risk.*
├── src.decision.*
│
├── src.api.main (FastAPI)
└── src.bot.telegram_bot (optional)
```

## Request Lifecycle

### Cache Hit Path

1. Page calls `svc.get_market_summary()`
2. DataService checks TieredCache for key `market_summary`
3. Memory cache hit → return immediately (< 1ms)
4. Disk cache hit → promote to memory → return (< 5ms)

### Cache Miss Path

1. Page calls `svc.get_market_summary()`
2. DataService checks TieredCache → miss
3. DataService calls HybridProvider
4. HybridProvider queries health monitor for best provider
5. API request → rate limiter acquires token → HTTP GET
6. Response parsed into MarketSummary dataclass
7. Stored in TieredCache (memory + disk)
8. Returned to page (< 500ms)

### Fallback Path

1. APIProvider raises TimeoutError
2. HybridProvider catches, logs, moves to next provider
3. CSVProvider attempts to load from data/*.csv
4. If CSV succeeds → parse → cache → return
5. If CSV fails → return MarketSummary.empty()
6. DataService never raises — empty default returned

## Provider Selection Algorithm

```
_input_: list of providers, health monitor
_output_: provider to use

1. healthy = [p for p in providers if health_monitor.is_healthy(p.name)]
2. if healthy is empty:
3.     use original list (override disabled state)
4. else:
5.     sort healthy by success_rate descending
6.     use first (healthiest) provider
7. try provider._do_*(...)
8. if fails: log warning, move to next
9. if all fail: return empty() default
```

## Cache Key Structure

| Key Pattern | Example | TTL |
|-------------|---------|-----|
| `market_summary` | `market_summary` | 30s |
| `market_status` | `market_status` | 30s |
| `top_gainers:{limit}` | `top_gainers:5` | 30s |
| `top_losers:{limit}` | `top_losers:5` | 30s |
| `top_turnover:{limit}` | `top_turnover:5` | 30s |
| `stock:{symbol}` | `stock:NABIL` | 30s |
| `history:{symbol}:{days}` | `history:NABIL:365` | 300s |
| `nepse_index:{days}` | `nepse_index:500` | 300s |
| `scan:{cache_key}` | `scan:...` | 30s |
| `watchlist` | `watchlist` | 30s |

## WebSocket Flow

```
LiveMarketStream
    │
    ├── connect(url)
    │       │
    │       ├── on_open: start heartbeat timer (30s)
    │       ├── on_message: parse JSON → dispatch subscribers
    │       ├── on_error: log, schedule reconnect
    │       └── on_close: schedule reconnect
    │
    ├── subscribe(callback)
    │       └── add to subscriber list
    │
    ├── unsubscribe(callback)
    │       └── remove from subscriber list
    │
    └── disconnect()
            └── stop heartbeat, close socket, cancel reconnect

Reconnect: exponential backoff (1s → 2s → 4s → ... → 60s max)
Fallback: DataService polls API at configured interval
```

## Configuration

All settings in `src/config.py`, overridable via environment variables or `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBSOCKET_ENABLED` | `False` | Enable WebSocket live feed |
| `WEBSOCKET_URL` | `""` | WebSocket server URL |
| `RATE_LIMIT` | `10` | Max tokens per provider |
| `API_TIMEOUT` | `15` | HTTP request timeout (seconds) |
| `RETRY_COUNT` | `3` | Max retries on failure |
| `BACKOFF_BASE` | `1.0` | Initial backoff (seconds) |
| `CACHE_MEMORY_TTL` | `60` | Memory cache TTL (seconds) |
| `CACHE_DISK_TTL` | `300` | Disk cache TTL (seconds) |
| `CACHE_REFRESH_INTERVAL` | `60` | Background refresh interval (seconds) |
| `ENABLE_PERFORMANCE_MONITORING` | `False` | Enable metrics collection |
| `LOG_LEVEL` | `INFO` | Logging level |
| `CORS_ORIGINS` | `http://localhost:8501` | Allowed CORS origins (comma-separated) |
| `NEPSE_API_BASE_URL` | `""` | Custom NEPSE API base URL |

## Project Structure

```
src/
├── alerts/           Alert rules, notification centre
├── api/              FastAPI REST endpoints
├── backtest/         Backtest engine, trade simulator, metrics
├── bot/              Optional Telegram client
├── config/           Production config, user settings
├── data/             DataService, providers, cache, WebSocket, metrics, health
├── dashboard/        Dashboard charts, reports, exporter
├── decision/         Signal engine, confidence, risk
├── engine/           Core analysis pipeline
├── indicators/       Technical indicators
├── logging/          Centralised logging
├── market_structure/ Support/resistance, levels
├── optimization/     Parameter optimiser, walk-forward
├── paper_trading/    Paper trading engine
├── portfolio/        Portfolio database, analytics
├── regime/           Market regime detector
├── replay/           Market replay engine
├── recommendations/  Trade recommendations
├── risk/             Risk management
├── scanner/          Market scanner, ranking
├── services/         Market service layer
├── signals/          Signal scoring
├── strategies/       Trading strategies
├── trading/          Trade journal
├── ui/               Streamlit frontend (pages, components, theme)
├── validators/       Data validation
└── watchlist/        Watchlist management
```
