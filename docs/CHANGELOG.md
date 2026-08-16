# Changelog

All notable changes to the NEPSE Quant Engine project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v1.0.0-rc8] — 2026-08-16

### Added — Sprint 13.9 (Deployment Hardening, Release Engineering & Operational Continuity)

- **Release manifest** (`data/state/release_manifest.json`).  Deterministic
  machine-readable record of version, source revision, build id, dependency
  declarations, Python compatibility, and config/calendar schema versions.
  Generated atomically with fsync by `scripts/build_release_manifest.py`;
  verified by `src/utils/release_manifest.verify_manifest`.  Contains no
  secrets (`src/utils/release_manifest.py`).
- **Readiness vs liveness probes** — `GET /health/live` (process alive) and
  `GET /health/ready` (503 until configuration validates, the governed
  calendar loads, and the cache round-trips; provider outages are
  informational and never flip readiness).  Legacy `GET /` unchanged
  (`src/api/health.py`).  Docker api healthcheck moved to `/health/live`.
- **Configuration validation** — `src/config/validation.py` layers semantic
  range/coherence checks on the existing `src.config` defaults (no new
  subsystem): positive scalars, MACD coherence, percentage bounds, URL
  schemes, explicit boolean values, and the `SECOND_PROVIDER_URL` opt-in
  contract.  Never raises, never includes values (secrets) in messages;
  feeds `/health/ready`.  `CONFIG_SCHEMA_VERSION` added to `src.config`.
- **Persistent-state inventory** — `src/utils/state_inventory.py` registry
  (owner, format, versioning, atomicity, locking, corruption policy,
  backup/migration/rollback for 11 stores) + `docs/STATE_INVENTORY.md`.
- **fsync-aware atomic writes** — `save_json(..., fsync=True)` for
  critical, low-frequency state (calendar activation, release manifest);
  hot paths unchanged (`src/utils/json_store.py`).
- **Release artifact pipeline** — `scripts/build_source_tarball.py`,
  `scripts/build_release_artifact.sh`, `scripts/verify_release_artifact.py`,
  `src/utils/release_artifact.py`: content-complete source artifact with
  secret/local-path/junk scanning and required-file/test enforcement.
  The exclusion list also drops local workspace metadata (`.freebuff/`),
  the generated synthetic benchmark corpus (`data/raw/syn*.csv`), and
  gitignored runtime/build state (`worker_metrics.json`,
  `nepse_calendar_history.json`, `release_manifest.json`), so an
  artifact built from a dirty tree matches one built from a clean
  checkout (Sprint 13.9 §3/§4).
- **Operational runbook** — `docs/OPERATIONS.md` (startup, shutdown,
  provider failure/recovery, data-quality degradation, calendar
  update/rollback, cache/state corruption, worker failure, restart,
  upgrade, rollback, incident investigation — each symptom → check →
  action → expected → escalation).
- **Release checklist** — `docs/RELEASE_CHECKLIST.md` covering tests,
  performance, security, configuration, state, calendar, providers,
  Docker, API, scanner, alerts, monitoring, rollback, artifact and git.
- **CI release validation** — `.github/workflows/release.yml`: clean
  checkout → install → Sprint 13.x regression + performance gate →
  artifact build + manifest + smoke test (boot uvicorn, probe
  `/health/live`, `/health/ready`, `/`, graceful shutdown).  Kept
  separate from the fast PR test job and the deep soak.
- **Sprint 13.9 test suite** — `tests/test_sprint13_9.py` (100+ tests):
  clean startup, invalid configuration, readiness/liveness, graceful
  shutdown, crash recovery, state inventory, corruption matrix, atomic
  writes, backup/restore, calendar/watchlist/cache/alert migration,
  provenance persistence, upgrade compatibility, rollback, Docker static
  audit, two-worker deployment, metrics/operational-status bounds,
  secret/configuration checks, release-artifact integrity, dependency
  reproducibility, performance-constant and Sprint 13.8 regressions.

### Notes

- **VERSION file is stale** — `VERSION` says `1.0.0-rc1` while this
  changelog is at `v1.0.0-rc7`.  Bump VERSION before tagging (see
  `docs/RELEASE_CHECKLIST.md`).
- Docker **runtime** validation (compose build/up, restart, two-worker
  release artifact) is covered by static audit + the `release.yml` smoke
  job; a full container run requires a Docker host.

---

## [v1.0.0-rc7] — 2026-08-16

### Added — Sprint 13.8 (Platform Soak Testing & Performance Gate Hardening)

- **Performance-gate flake root-caused and eliminated without weakening any
  threshold.** The Sprint 13.2 cold/warm speedup flake was traced to two
  harness defects, not the engine: (1) `ScannerCache._file_fingerprint`
  performed a `Path.resolve()` filesystem syscall per file per rep — pure
  fixed overhead that inflated both legs but hurt the light warm leg
  disproportionately under I/O interference; removed (fingerprints are
  content+stat based, resolution adds no change-detection value).
  (2) Measurement legs ran 5 consecutive cold reps then 5 consecutive warm
  reps, so a slow machine phase could hit only one leg and corrupt the
  ratio.  Legs now use **interleaved cold/warm pairing** — each pair samples
  the same machine conditions, and the reported ratio is the median of
  per-pair ratios.  Verified: 10/10 isolated gate runs, 3/3 post-fix runs,
  plus a controlled 4-way CPU-contention run, all PASS (speedup 14.8–22.3x
  vs the 5x requirement; warm API ratio 0.590–0.677 vs ≤0.75 cap; warm
  portfolio ratio 0.619–0.653 vs ≤0.75 cap; analyze p99 65.8–189.8 ms vs
  ≤2000 ms cap).  No acceptance threshold changed.
- **Environmental contention detection with a single justified rerun**
  (`benchmarks/ci_gate.py`) — the gate probes wall-vs-CPU-time ratio and an
  alert-history file I/O micro-benchmark at pre/mid/post leg boundaries and
  between every cold/warm pair.  A failing pass reruns **once** only when a
  probe exceeds its documented threshold (wall/cpu ≥ 1.5, I/O ≥ 45 ms); the
  rerun applies the identical 5x / 0.75 / 0.75 / 2000 ms requirements.
  This is not "retry until lucky": a rerun is triggered only by a measured
  environmental signal, and the same gate must pass on the retry.  The I/O
  probe is a bounded alert-history read+write (the dominant fixed per-call
  cost in the light warm legs) sampled min-of-3 per point so single-sample
  AV jitter (which spikes to ~80 ms on this box) cannot false-trigger.
- **Soak framework** (`benchmarks/soak.py`) — bounded, hermetic, offline
  platform soak.  `SOAK_ITERATIONS` / `--iterations` configurable
  (default 25); CI keeps a short bounded run, deeper runs are opt-in.
  Five scenarios: **A** normal `get_history → analyze → scan → metrics`
  loop; **B** primary healthy → unavailable → fallback → recovery;
  **C** AGREE / MATERIAL / AGREE / MAPPING_CONFLICT / AGREE injection;
  **D** cold / warm / invalidate / cold / warm cache cycles;
  **E** worker start → request → stop → restart → request.  Every scenario
  runs the real hermetic in-process pipeline (the same code path the CI
  gate exercises), never a simulation.  Reports per-scenario verdicts,
  bounded-state deltas (incidents, minor symbols, quality snapshots,
  notifications, scanner cache), resource deltas (threads, open file
  objects, temp/lock/corrupt litter), and a tracemalloc memory envelope
  (plateau median vs end, 8 MiB cap).
- **Hermetic soak baselines** — the soak resets exactly the process-global
  bounded state it measures (`reconciliation_metrics`, `quality_trends`,
  incident ring, notification centre, caches) at entry so suite residue
  from earlier tests cannot fail absolute bound checks, and clears the
  process-wide scanner/indicator caches at exit.  Temp workspaces are
  always removed on both success and failure paths.
- **Windows pytest session-crash fixed** — the full-suite session crashed
  on Windows when pytest tried to clean the system `pytest-current`
  junction (a broken/stale junction in the OS temp dir).  Tests now use a
  project-local `basetemp` (`.pytest_tmp/`, gitignored) via a
  `pytest_configure` hook in `tests/conftest.py`, which also isolates all
  suite temp state inside the repo (no system-temp litter).
- **Two-worker aggregation race fixed** (`tests/test_sprint13_2.py`) — the
  worker-metrics aggregation test asserted the aggregate equals the local
  request counter immediately, but worker self-reports are throttled to
  every 5 s, so the aggregate can legitimately lag by one window.  The test
  now settles the report throttle before asserting (harness fix; no
  production change, no threshold change).
- **Soak-in-suite contamination fixed** — `test_temp_dirs_cleaned_by_soak`
  failed in-suite because prior tests legitimately populated the
  process-global incident ring; resolved by the hermetic baseline reset
  above (test passes alone and in-suite).
- **Measurement-hardening of a Sprint 12 test** —
  `test_portfolio_times_includes_warm_percentiles` asserted a cold/warm
  ratio from only 2 reps (median-of-2 has no outlier resistance, flipped to
  1.078 under transient load).  Bumped to the gate's 5-rep protocol — the
  ratio is now stable at 0.49–0.61.  Assertion (warm < cold) unchanged.
- **Regression tests** (`tests/test_sprint13_8.py`, now 102 tests):
  load-leg statistic is median-not-min, fingerprint change behaviour
  (content+stat, no per-call resolve), I/O-probe trigger + smoke tests,
  contention protocol (rerun used only when a probe exceeds threshold),
  soak scenarios, memory bounds, file/resource leaks, subprocess cleanup,
  metrics/incident/notification bounds.

### Verified (measured, this sprint)

- **Full suite: 3051 passed, 2 skipped** (3053 collected) — includes the
  102-test Sprint 13.8 file, 57-test Sprint 13.2 file, and the 744-test
  combined 13.2–13.8 + performance regression.
- **Soak: 60 iterations PASS** — 269 s, zero state growth (threads +0,
  file objects +0, incidents +0, minor symbols +0, quality snapshots +0,
  notifications +0), no temp/lock/corrupt litter, memory growth 0.077 MiB
  (plateau 2.128 → end 2.205 MiB, 8.0 MiB envelope; growth decelerates and
  plateaus — end-of-soak memory at 60 iterations ≈ 25 iterations).
- **Benchmark reproducibility** — 10/10 + 3/3 gate runs PASS; controlled
  4-way CPU contention run PASS with the environmental signal measured and
  recorded (wall/cpu 1.293, I/O probe 66 ms > 60 ms threshold at the time;
  threshold since tightened to 45 ms with between-pair probes).
- **No state contamination** across provider failure/recovery, fallback,
  minor/material/mapping conflicts, calendar validation, cache hit/miss,
  worker restart, and notification activity under repeated execution.

---

## [v1.0.0-rc6] — 2026-08-14

### Added — Sprint 13.6 (Production Readiness, Provider Diversity & Operational Observability)

- **Second-provider adapter** (`src/data/providers.py`) — `GitHubCSVProvider`
  implements the existing `BaseProvider` interface; disabled unless
  `SECOND_PROVIDER_URL` is set (empty URL keeps the chain byte-identical).
  **Live two-provider reconciliation remains NOT AVAILABLE** — no second
  independent source has been validated here; the adapter + controlled
  harness prove the architecture is provider-ready without claiming live
  multi-provider verification.
- **Provider reliability history** (`src/data/health.py`) — bounded per-provider
  outcome window, `record_success/timeout/malformed/failure/empty`, degradation
  states `HEALTHY/DEGRADED/UNAVAILABLE/UNKNOWN` with configurable triggers,
  automatic recovery, and compact `reliability_history()` snapshots.
- **Incident tracking** (`src/data/incidents.py`, NEW) — bounded event ring
  (200 max), per-kind counters, repeated-minor detection; material/mapping/
  timeout/fallback/identity incidents observable via `/metrics`.
- **Data-quality trend observability** (`src/data/quality.py`) — bounded
  `QualityTrendTracker` with previous/current/delta corpus aggregates.
- **Calendar governance workflow** (`src/data/calendar.py`) —
  `validate_candidate_calendar` (schema + overlap-compatibility + weekend
  regression), `update_calendar` (validate → backup → atomic activate →
  version history), `rollback_calendar` (never blocked by an incompatible
  current calendar; reversible).
- **Operational status** (`src/data/operational_status.py`, NEW) — derived
  `system_status()` (HEALTHY/DEGRADED/UNAVAILABLE) from existing state; no
  duplicate monitoring subsystem.
- **/metrics blocks** (`src/api/metrics.py`) — bounded `provider_health`
  (reliability + degradation), `reconciliation.incidents`,
  `data_quality.trend`, `calendar.updates`, `system_status`; backward
  compatible and safe when empty.
- **Tests** — `tests/test_sprint13_6.py` (136 tests, 21 categories):
  second-provider adapter, degradation/recovery, incidents, quality trends,
  calendar update/rollback, cache restart/corruption, signal safety,
  scanner/alert safety, metrics compatibility, bounded stability, Docker
  config, regression coverage.

### Notes

- Provider degradation is deliberately conservative: a single failure never
  disables a provider; thresholds are configurable; recovery is automatic.
- Conflicted outcomes are never cached as trusted; fallback and reconciliation
  provenance survive process restart.
- Performance gate passes in isolation: warm API ratio 0.413, warm portfolio
  ratio 0.576 (max 0.75), analyze p99 105.46 ms (max 2000 ms).

---

## [v1.0.0-rc5] — 2026-08-14

### Added — Sprint 13.5 (Production Data Trust, Calendar Governance & Reliability)

- **Benchmark/test determinism** — `bench_startup()` records subprocess
  failures instead of raising (the informational startup metric can never
  fail the runner); `benchmark_isolation` context manager (`benchmarks/common.py`)
  redirects alert/portfolio persistence to a temp dir, snapshots/restores the
  scanner `DATA_DIRECTORY`, clears the scanner cache on entry/exit, resets the
  `DataService` singleton, and serialises the global-mutating sections with an
  `RLock` so concurrent benchmark runs are safe.  `scripts/benchmarks.py::run_all`
  runs its corpus legs inside the isolation context.  Six regression tests
  (`test_benchmark_runner_isolated_state`, `_repeatable`, `_concurrent`,
  `_startup_error`, plus smoke + isolation-context tests) prove isolated /
  repeated / concurrent execution — the full-suite timing flake is eliminated.
- **Versioned, provenance-aware NEPSE trading calendar** (`src/data/calendar.py`
  v2 + `data/state/nepse_calendar.json`) — governed schema: `calendar_version`
  (supported `1.0`/`2.0`), `effective_from/effective_to`, `timezone`,
  `trading_week`, explicit `holidays`, `special_sessions` (trading days on
  weekends/holidays), `closures` (exceptional closures), `provenance`,
  `last_validated`, `source`, `operator_notes`.  Holidays are **not** hard-coded
  in Python; the shipped baseline documents the Sun–Thu weekend rule with honest
  provenance and zero fabricated holidays.  `classify_date` returns
  `SPECIAL_SESSION` / `EXCEPTIONAL_CLOSURE`; `is_trading_day` respects both.
- **Calendar governance** — `validate_calendar_data` returns diagnostics (never
  raises, including on non-mapping payloads): unsupported versions, invalid date
  ranges, malformed/duplicate entries, contradictory holiday/session/closure
  definitions, out-of-range weekdays (checked pre-modulo so `9` is rejected,
  not silently mapped to Tuesday), redundant weekend closures, and missing
  provenance.  `NepseCalendar.load` **fails safe**: a malformed calendar is
  rejected with logged diagnostics and the base weekend-rule calendar is used —
  a broken calendar can never silently turn an exchange closure into a normal
  trading day.  Bounded `to_status()` / `coverage_for()` expose version / window /
  last-validated / source / counts — never raw date lists (safe for `/metrics`).
- **Data provenance / trust state** (`src/data/provenance.py` + `StockHistory.provenance`)
  — compact, bounded metadata attached to every history result and analysis:
  trust states `trusted / reconciled / single_provider / fallback /
  partially_reconciled / conflicted / unavailable / stale / calendar_invalid /
  quarantined`; `resolve_trust` is worst-state-wins; `provenance_from_history` /
  `provenance_from_reconciliation` build the state from pipeline signals
  (including `calendar_valid=False` derived from calendar-invalid records).
  The trust state survives Provider → DataService → Reconciliation → Quality →
  Analyzer → Signal: `get_history` / `get_history_batch` (incl. cache hits) and
  `get_reconciled_history` attach provenance, and `analyze_dataframe` suppresses
  the signal to HOLD for any unsafe trust (conflicted / unavailable /
  calendar-invalid / quarantined).
- **Reconciliation observability** — `/metrics` gains bounded `provider_health`
  (per-provider enabled / success-rate / requests / failures / consecutive
  failures / last-success age / latency from the existing `ProviderHealthMonitor`
  — no second health architecture) and bounded `calendar` status blocks.  The
  existing `reconciliation` + `data_quality` scalar counters remain; nothing
  unbounded is exposed.
- **Cache safety** — reconciled entries embed their reconciliation state and a
  `MATERIAL_DISAGREEMENT` / `MAPPING_CONFLICT` outcome is **never** cached as a
  trusted frame; fallback-served data carries `fallback_used=True` provenance so
  it can never masquerade as primary data.  `TieredCache`/`MemoryCache`/`DiskCache`
  gained `delete_prefix` (with an alphanumeric boundary guard so clearing
  `reconciled:NABIL:365` never deletes `reconciled:NABIL:3650`);
  `DataService.clear_reconciled_cache()` does targeted invalidation (exact-key
  delete for a symbol+horizon, prefix delete otherwise) without evicting the rest
  of the cache.
- **Calendar-aware quality** — `assess_history(..., calendar=...)` flags rows on
  governed non-trading days (weekend / configured holiday / exceptional closure)
  as `R_NON_TRADING_DATE` error-level issues with a `calendar_invalid_records`
  counter (double-count-guarded against the price/date invalid masks).  Special
  sessions are trading days; UNKNOWN days are reported via `unknown_sessions` but
  never flagged.  New listings do not raise false missing-session alarms.
- **Reliability hardening** — `HybridProvider.fallback_used` tracks the *current*
  request's fallback (resets per call, so a recovered primary is never mislabelled);
  `reconcile_history` returns a canonical empty frame (no `KeyError`) when every
  date is materially conflicted; `validate_calendar_data` never crashes on junk.
- **Sprint 13.5 test suite** (`tests/test_sprint13_5.py`) — 121 behavioral tests
  across 17 categories (benchmark determinism, calendar loading/validation/
  provenance/holidays/special sessions, data provenance, provider health,
  reconciliation + fallback metrics, failure injection for providers/
  reconciliation/calendar/cache, cache provenance + safety, signal safety, API
  compatibility, no-silent-degradation, calendar-aware quality, and Sprint 13.4
  regression), including a real-corpus gate (skipped when the corpus is absent)
  and an end-to-end calendar-invalid → signal-suppression proof.

---

## [v1.0.0-rc4] — 2026-08-13

### Added — Sprint 13.4 (Cross-Provider Reconciliation & Trading Calendar Integrity)
- **Cross-provider reconciliation** (`src/data/reconciliation.py`) — the engine now moves from *provider priority wins* to *reconcile before trust*.  Multiple providers' records for the same symbol/date are normalized to canonical OHLCV fields and compared field-by-field with per-field tolerances: price fields use a tight relative bound (default 1%) while volume uses a wider one (default 10%) — never one fixed percentage for every field (all configurable).  Explicit statuses: ``AGREE`` (validated data), ``MINOR_DISAGREEMENT`` (resolved to the documented preferred source + warning — **never averaged**), ``MATERIAL_DISAGREEMENT`` (never becomes a trusted record), ``UNAVAILABLE`` (fallback), ``MAPPING_CONFLICT`` (records for different securities are never compared).
- **Reconciliation policy** — AGREE → validated data; MINOR → preferred source + warning/provenance; MATERIAL → conflicting dates dropped from merged history and quarantined (the record never silently becomes a normal signal); UNAVAILABLE → fallback; MAPPING_CONFLICT → no signal.  ``ReconciliationResult`` carries structured provenance (status, symbol, date, providers, selected_source, disagreement_fields, difference_metrics, warnings) and never exposes raw provider payloads.
- **Symbol/company mapping validation** — ``check_symbol_mapping`` verifies every provider record refers to the *same security* (canonical symbol + company identity) *before* any value comparison; a mismatched identifier or a company-name disagreement yields ``MAPPING_CONFLICT``.
- **Corporate-action awareness** — ``classify_price_jump`` distinguishes a *large-but-potentially-legitimate* move (dividend / right / bonus / split; threshold ``RECONCILE_CORPORATE_ACTION_MOVE_PCT=20``) from *structurally-impossible* OHLC; a large-but-sound move is flagged and annotated, never quarantined by the reconciliation layer.
- **History-wide reconciliation** — ``reconcile_history`` merges full frames from all providers per-date (dtype-safe Date handling, canonical capitalized OHLCV output); materially-conflicting dates are dropped (never averaged) and reported; ``HybridProvider.get_reconciled_history`` queries **every** healthy provider (additive — the fail-fast ``get_history`` fallback chain is untouched) with per-provider validation and bounded metrics; ``DataService.get_reconciled_history`` returns the trusted frame **with provenance attached**.
- **Cache provenance** — reconciled frames are cached under a dedicated namespace with their reconciliation state; a ``None`` frame is never cached, a previously-conflicted record can never silently become trusted data, and a refresh after conflict serves the new reconciled state.
- **Signal safety** — ``analyze_dataframe(..., reconciliation=...)`` attaches an additive ``reconciliation`` block; an unresolved ``MATERIAL_DISAGREEMENT`` / ``MAPPING_CONFLICT`` suppresses the signal to HOLD (``signal_suppression_reason`` reflects the reconciliation state) — a provider conflict never silently becomes a BUY/SELL, even on an otherwise VALID frame.
- **NEPSE trading calendar** (`src/data/calendar.py`) — centralized calendar abstraction with explicit provenance: NEPSE trades **Sunday–Thursday** (Friday/Saturday closed), operator-maintained versioned holidays (none hard-coded without provenance), and **corpus-derived observed sessions** as the strongest available evidence.  Day classification ``TRADING_DAY / HOLIDAY / WEEKEND / UNKNOWN``; gap classification distinguishes ``EXPECTED_NON_TRADING_DAY`` (weekend/holiday/corpus-verified closure) from ``MISSING_TRADING_SESSION`` (scheduled session with no record, confirmed by the corpus) and ``UNKNOWN`` (no evidence — the engine prefers explicit uncertainty over guessing).  ``expected_sessions`` / ``previous_trading_day`` / ``next_trading_day`` / versioned JSON persistence.
- **Calendar-aware continuity** — ``assess_history(..., calendar=...)`` uses trading-session semantics for gap detection: a Friday/Saturday closure is never reported as missing data, while a genuinely-missing scheduled session increments ``report.missing_sessions``.  The legacy business-day heuristic remains the default for existing callers (byte-identical behaviour); the corpus report and reconciliation paths pass the calendar explicitly.
- **Bounded reconciliation metrics** — thread-safe ``ReconciliationMetricsCollector`` (provider_requests/successes/failures/timeouts, fallback_count, reconciliation_checks, agreements, minor/material disagreements, mapping_conflicts, quarantined_conflicts) surfaced through ``GET /metrics`` under the additive ``reconciliation`` block; scalar-only, all existing keys preserved.
- **Config** — ``RECONCILE_PRICE_TOLERANCE_PCT=1.0``, ``RECONCILE_VOLUME_TOLERANCE_PCT=10.0``, ``RECONCILE_MATERIAL_PRICE_PCT=5.0``, ``RECONCILE_MATERIAL_VOLUME_PCT=50.0``, ``RECONCILE_PREFERRED_SOURCE=api``, ``RECONCILE_CORPORATE_ACTION_MOVE_PCT=20.0``, ``NEPSE_CALENDAR_FILE=data/state/nepse_calendar.json``.
- **Sprint 13.4 test suite** (`tests/test_sprint13_4.py`) — 94 tests covering repair/idempotency, reconciliation (identical / small / material / unavailable / timeout / malformed / volume vs price / multiple providers), symbol mapping, corporate actions, the trading calendar (weekend / holiday / unknown / missing / consecutive missing / future date), history-wide reconciliation, bounded metrics, signal safety (AGREE / MINOR / MATERIAL / MAPPING / UNAVAILABLE), cache provenance, scanner mixed-quality behaviour, the /metrics reconciliation block and the additive API contract.

### Notes
- The 65 Sprint 13.3 quarantined CSVs were repaired (zero-price 2026-07-24 rows removed, backup preserved, idempotent) — 2026-07-24 is a **Friday** (NEPSE is closed Fri/Sat), so the repair created **no** missing trading session under the calendar.
- Live corpus validation (calendar-aware): 286 symbols / 19,386 records — 277 VALID, 0 INVALID, 9 SUSPICIOUS (stale), 0 duplicates, 0 conflicts; 223 missing trading sessions (new listings joining mid-window) vs 11,947 expected non-trading days correctly classified.
- Cross-provider disagreement is now *detected* before trust, but only providers actually configured in the chain are compared: with a single live API source the reconciliation layer is exercised via the CSV fallback and the test/validation harness — documented, not overclaimed.

---

## [v1.0.0-rc4] — 2026-08-13

### Added — Sprint 13.3 (Live Data Quality & Provider Reliability)
- **Canonical market-data contract** (`src/data/quality.py`) — the engine now validates every OHLCV payload that enters the platform against one documented contract (``symbol/date/open/high/low/close/volume``; optional fields never fail records).  States are machine-readable: ``VALID`` / ``INVALID`` / ``SUSPICIOUS`` and ``FRESH`` / ``STALE`` / ``UNKNOWN``, with stable reason codes (``non_positive_price``, ``nan_price``, ``infinite_price``, ``negative_volume``, ``high_below_open_close``, ``low_above_open_close``, ``high_below_low``, ``invalid_date``, ``future_date``, ``duplicate_date``, ``conflicting_duplicate``, ``unexpected_gap``, ``stale_data``, …).  Validation is **centralized** — providers, loaders and the API never scatter ad-hoc checks — and never exposes raw exception tracebacks.
- **OHLC integrity** — vectorized checks for positive finite prices, non-negative volume, NaN/inf rejection and the high/low/open/close relationships; row-level ``validate_ohlcv_record`` covers the same contract for single-record callers.
- **Duplicate & conflict detection** — identical duplicates on the same trading date are deduplicated safely (warning); disagreeing duplicates are flagged as ``CONFLICT`` (error) and never silently resolved.
- **Continuity & freshness** — business-day gaps wider than ``DATA_MAX_GAP_DAYS`` (14) are reported without inventing candles; the latest record's age is classified ``FRESH``/``STALE`` against ``DATA_STALE_AFTER_DAYS`` (7); a stale price can never automatically generate a fresh BUY/SELL signal (``ENFORCE_SIGNAL_FRESHNESS`` default off for backward compatibility, enforced signal suppression when enabled).
- **Provider fallback validation** (`src/data/providers.py`, `src/data/service.py`) — ``HybridProvider`` gained a ``data_validator`` hook: malformed provider payloads (broken OHLC, NaN, conflicts) raise ``InvalidDataError`` and the hybrid falls through to the next provider; the CSV fallback and last-known-value semantics are preserved; ``CSVProvider._discover_csv_files`` deduplicates by symbol stem across layout patterns so one symbol is never analysed twice.
- **Live HTTP timeout test** (`tests/test_sprint13_3.py::TestLiveTimeout`) — a controlled local ``ThreadingHTTPServer`` deliberately delays past the configured ``API_TIMEOUT``; verified ``ProviderTimeout`` is raised, the hybrid falls back to CSV (timeout/failure metrics bump), the API stays responsive, and no handler/serve threads leak.
- **Symbol-mapping integrity** — normalization (uppercase/strip), empty/whitespace/unsafe-symbol checks, case-insensitive CSV resolution, unknown/delisted symbols resolve to ``None`` (never another company's data), and stem-deduplication is tested.
- **Market-status zero-vs-unknown** — ``/market/status`` and ``/market-status`` now distinguish a genuine zero (real market print) from an unknown/unavailable summary: the payload carries an additive ``data_quality`` block (``available`` / ``status`` / ``unknown_fields`` / ``as_of`` / ``age_seconds`` / ``freshness`` / ``source``); a provider failure is never rendered as ``NEPSE 0.00``.
- **Signal safety** — invalid/conflicting data suppresses the signal to ``HOLD`` with ``signal_suppressed=True`` and ``signal_suppression_reason``; stale data is flagged and (when enforcement is on) suppressed; every analysis and the market-status payload expose ``data_quality`` additively, backward-compatible.
- **Scanner safety** — files failing the canonical contract move to ``skipped`` (with machine-readable reasons) instead of ranked results; healthy symbols keep deterministic ranking; INVALID analyses are still cached so warm scans remain pure cache hits; INVALID symbols are excluded from the alert batch.
- **Bounded quality metrics** — thread-safe ``QualityMetricsCollector`` (records_checked/valid/invalid/suspicious, duplicates, conflicts, stale_records, provider_failures/timeouts, fallback_count) surfaced through ``GET /metrics`` under ``data_quality``; scalar-only, no unbounded per-record history.
- **Data-quality report CLI** — ``python -m src.data.quality --data-dir data/raw`` validates every CSV and prints a per-symbol table plus aggregates; exits non-zero when critical conditions (INVALID records or conflicts) are present, ``--allow-invalid`` for report-only.
- **Config** — ``DATA_STALE_AFTER_DAYS=7``, ``DATA_MAX_GAP_DAYS=14``, ``ENFORCE_SIGNAL_FRESHNESS=false`` (env-overridable).
- **Sprint 13.3 test suite** (`tests/test_sprint13_3.py`) — 88 tests covering OHLC integrity, dates, duplicates/conflicts, continuity, freshness, symbols, market summary, quotes, provider disagreement, provider validation/fallback, quality metrics, signal safety, scanner safety, cache freshness, live timeout, market-status API contract, analyze API contract and the data-quality CLI.

### Notes
- Validator entry points: ``assess_history(df, symbol=…)``, ``validate_history_frame``, ``assess_quote``, ``assess_market_summary``, ``detect_disagreement``, ``validate_corpus``.
- Provider disagreement policy: the hybrid prefers the first validated provider in priority order; ``detect_disagreement`` flags material >1% differences — the engine never silently averages conflicting prices.
- Real-corpus report (2026-08-13): 286 symbols / 19,451 records — 212 VALID, 65 INVALID (each carrying one zero-price row on 2026-07-24 from the yonepse refresh), 9 SUSPICIOUS (stale), 0 duplicates, 0 conflicts.  The 65 affected symbols are excluded from ranked results and their signals suppressed until the underlying rows are repaired.

---

## [v1.0.0-rc4] — 2026-08-12

### Added — Sprint 13.2 (Multi-Worker Reliability & Failure-Path Hardening)
- **Live 2-worker validation** (`tests/test_sprint13_2.py::TestLiveTwoWorker`) — spawns the exact Docker API entrypoint (`uvicorn src.api.main:app --workers=2`) against an isolated temp corpus/state root and verifies: all core endpoints serve 200, both workers self-report and aggregate with sum semantics (aggregate ≥ local, no double counting), killing one worker leaves the fleet serving and uvicorn respawns a fresh pid, and a dead-provider run degrades `/market/status` to a controlled empty summary while `/analyze` keeps serving via CSV fallback
- **Provider timeout path** — `APIProvider` now uses the documented `API_TIMEOUT` config (was hard-coded 15 s, ignoring the Docker setting); timeouts raise a distinguishable `ProviderTimeout` (a `ProviderError` subclass) via a failure-kind tracker instead of degrading into a generic error; `BaseProvider.get_*` wrappers preserve the distinction end-to-end; the history loop's fail-fast threshold also surfaces `ProviderTimeout`
- **Provider failure matrix tests** — timeout / connection error / HTTP error / malformed response / empty response / all-providers-fail, each with the expected controlled behaviour and metric accounting (`provider_failures` increments; hybrid falls back to CSV; service returns empty history/summary, never crashes)
- **Worker metrics aggregation extension** (`src/utils/worker_metrics.py`) — per-worker `api_requests` totals (requests/errors) self-reported alongside cache/lock counters; `aggregate.requests`/`aggregate.request_errors` are the fleet sums (each request served by exactly one worker → true aggregate); backward-compatible additive keys
- **Worker lifecycle observability** (`src/api/main.py`) — FastAPI lifespan logs `worker start pid=… hostname=…` / `worker stop pid=…` per worker process, making multi-worker deployments diagnosable from logs alone
- **API error-detail sanitization** (`src/api/analyze.py`, `portfolio.py`, `scanner.py`, `watchlist.py`) — 500 details no longer embed raw exception text (paths, tokens, internal URLs); stable prefixes preserved (tests assert prefixes, so no contract break)
- **Windows orphan-process fix** (`benchmarks/validate_workers.py`) — `_shutdown` now always runs `taskkill /T /F` on Windows after the graceful signal: multiprocessing-spawned uvicorn workers are not in the master's console group and previously survived its exit, holding state files open and poisoning later test runs
- **Process-level gate enforcement tests** — `python -m benchmarks.ci_gate` proven to exit 0 on PASS and non-zero on FAIL / invalid data; missing/malformed/NaN/inf/empty measurements fail explicitly (never silent PASS); threshold-boundary semantics pinned (`actual == threshold` → PASS, one unit above → FAIL) for all three ratio gates and the p99 tripwire
- **Failure cleanup tests** — locks released on exception, no `.tmp`/`.lock` litter after mutator/serialization failures, corrupt-store recovery backs up evidence, 8-thread concurrent `update_json` writes lose no updates
- **Dashboard resilience tests** — metrics page survives malformed/stale/zero/missing metrics with a clear no-workers/stale state
- **Sprint 13.2 test suite** (`tests/test_sprint13_2.py`) — 55 tests across worker-metrics safety, provider failure matrix, service timeout behaviour, failure cleanup, gate boundaries/invalid data/exit codes, API contract under failure, dashboard resilience, lifecycle logging, concurrent metrics stress, and live 2-worker validation

---

## [v1.0.0-rc4] — 2026-08-12

### Added — Sprint 13.1 (Production API & Dashboard Integration Hardening)
- **Bounded API request tracker** (`src/api/timing.py`) — thread-safe per-endpoint latency/error windows (deque-capped), wired into the FastAPI request-timing middleware; exposes p50/p95/p99, request and error counts per endpoint via ``GET /metrics`` under the new ``api_requests`` block
- **Performance gates in /metrics** — ``performance_gates`` block reports the enforced CI thresholds (warm API ratio 0.75, warm portfolio ratio 0.75, analyze p99 2000 ms) plus the last recorded ``benchmark-ci.json`` verdict
- **Fail-safe CI gate evaluation** (`benchmarks/ci_gate.py`) — pure ``evaluate_checks()`` turns missing/malformed/zero/non-finite measurements into explicit FAILs with reasons (no more silent ``ratio 0.0 <= max`` passes); artifacts now carry ``check_details``/``check_reasons``/``failures``; a failed run prints an actionable ``PERFORMANCE GATE FAILED`` block
- **Dashboard hardening** (`src/ui/pages/metrics_page.py`) — new sections for API request latency, scanner cache counters and performance-gate PASS/FAIL status, all served by pure ``summarize_*`` helpers tolerant of missing/malformed/stale payloads
- **CI-enforcement regression test** — proves the workflow actually invokes ``benchmarks.ci_gate`` so the gates can never silently become informational-only
- **API Explorer fix** (`src/api/explorer.py`) — ``list_endpoints`` now unwraps FastAPI lazy ``_IncludedRouter`` placeholders (re-applying ``include_context.prefix``), restoring real endpoint discovery (41 endpoints incl. ``/market/status``) instead of an empty list
- **Sprint 13.1 test suite** (`tests/test_sprint13_1.py`) — 36 tests: tracker bounds/thread-safety, /metrics block contract, gate pass/fail/missing/malformed/zero/stale/empty/small-sample, dashboard contract, multi-worker aggregation, production-path verification, legacy-path reachability, error hardening

---

## [v1.0.0-rc3] — 2026-08-06

### Added — Sprint 11 (Performance, Scalability & Observability)
- **Parallel market scanner** (`src/scanner/engine.py`) — ``scan_market`` now analyses every CSV concurrently with a bounded ``ThreadPoolExecutor`` (worker count from ``SCANNER_WORKERS``), with deterministic file-order output, per-file error isolation, and progress logging every ``SCANNER_LOG_PROGRESS_EVERY`` files
- **Scanner cache** (`src/cache/scanner_cache.py`) — thread-safe LRU+Ttl cache with two tiers (parsed DataFrames + analysis outputs), keyed by file fingerprint (path, mtime, size) and invalidated on source change / expiry / indicator-configuration change; bounded by ``SCANNER_CACHE_MAX_ENTRIES``; exposes hit/miss metrics
- **CSV parse cache** (`src/loaders/csv_loader.py`) — ``load_csv`` consults the scanner cache so unchanged files are never re-parsed (callers must treat returned frames as read-only)
- **Indicator in-place chaining** (`src/indicators/*`, `src/engine/analyzer.py`) — every ``add_*`` gained an ``inplace=False`` keyword; ``analyze_dataframe`` now makes exactly **one** defensive copy and chains all indicator steps in-place (previously ~8 copies per stock); ATR replaced the temporary ``pd.concat(...).max(axis=1)`` frame with element-wise ``np.fmax`` (identical numerics)
- **Historical data optimisation** (`src/data/service.py`) — ``get_history`` TTL now configurable via ``HISTORY_CACHE_TTL``; new ``get_history_batch(symbols, days)`` fetches only cache misses; the live-market scan fallback uses batch retrieval
- **Metrics endpoint** (`src/api/metrics.py`) — ``GET /metrics`` exposes DataService request metrics, cache hit ratio, scanner cache stats, provider stats and best-effort memory usage
- **Request timing middleware** (`src/api/main.py`) — structured latency log per HTTP request
- **Timing helper** (`src/logging/timing.py`) — ``timed()`` context manager for structured ``name=... duration_ms=...`` records
- **Alert state thread-safety** (`src/alerts/engine.py`) — ``process_alerts`` runs under an ``RLock`` so parallel scanner workers never corrupt ``data/alerts/history.json``
- **Benchmark suite** (`scripts/benchmarks.py`) — automated benchmarks for scan (cold/warm), analysis, history load (miss/hit/batch), backtest speed, API latency, subprocess startup and peak scan memory; JSON results stored under ``benchmarks/results/`` with before/after comparison
- **Performance regression tests** (`tests/test_performance.py`) — determinism, parallelism, cache hit/invalidation/LRU, indicator in-place equivalence, history batch/TTL, metrics endpoint, benchmark smoke tests

### Changed
- **Configurable performance settings** (`src/config.py`) — ``SCANNER_WORKERS``, ``SCANNER_CACHE_TTL``, ``SCANNER_CACHE_MAX_ENTRIES``, ``SCANNER_LOG_PROGRESS_EVERY``, ``HISTORY_CACHE_TTL``, ``BENCHMARK_RESULTS_DIR``/``BENCHMARK_SYMBOLS``/``BENCHMARK_ROWS``
- ``src/cache/market_cache.py`` — scan cache TTL derives from ``SCANNER_CACHE_TTL`` (backward-compatible ``CACHE_SECONDS`` alias preserved)
- ``src/data/service.py`` — ``scan_market`` cache TTL derives from ``SCANNER_CACHE_TTL``

### Notes
- Analysis cache hits return the first scan's ``new_alerts`` and do not re-run ``process_alerts`` (same semantics as the whole-scan ``market_cache``); clear the cache or touch the CSV to force a fresh alert pass

---

## [v1.0.0-rc2] — 2026-08-05

### Added
- **Canonical version module** (`src/version.py`) — single version source read from the root `VERSION` file; FastAPI metadata and the `src` package now report the same version
- **CSV path resolver** (`src/loaders/csv_loader.py::resolve_stock_csv_path`) — single authoritative symbol→CSV-path helper used by `api/analyze.py`, `api/backtest.py` and `watchlist/scanner.py`
- **Sprint 10 regression suite** (`tests/test_sprint10.py`) — momentum MA filter, confidence consolidation, single CSV loader, config and version consistency

### Changed
- **Momentum strategy fix (critical)**: `MomentumStrategy` now reads the engine's real indicator columns `SMA_20`/`SMA_50` instead of the non-existent `MA20`/`MA50`. The old silent `close` fallback disabled the moving-average trend filter entirely (BUY fired on RSI alone). Missing indicator columns now raise a clear `ValueError` instead of silently degrading
- **Confidence engine consolidation**: `src/decision/confidence.py` is the single implementation; legacy `src/decision/engine.py` is deprecated (kept working, emits `DeprecationWarning`, documents migration to `confidence.py` / `recommendations.trade_plan.py`)
- **Single CSV parser**: `CSVProvider._load_csv` now delegates to `src/loaders/csv_loader.py::load_csv` — one CSV parsing implementation for the whole platform
- **Configuration consolidation**: `DataService._default_api_urls` now derives from `src.config.DATA_SERVICE_API_URLS` (added the previously-missing `github_datasets` key); Telegram bot reads `API_BASE_URL`/`TELEGRAM_TOKEN` from `src.config` instead of re-reading env vars
- **Exception handling**: replaced redundant `except (NotImplementedError, Exception)` tuples, added logging to silent `return []` paths in `DataService` top-movers and provider request failures

### Removed / Deprecated
- **10 legacy ad-hoc smoke scripts** under `src/` (`src/test_decision.py`, `src/test_indicators.py`, `src/test_launcher_utils.py`, `src/test_market_scanner.py`, `src/test_momentum.py`, `src/test_phase54.py`, `src/test_pipeline.py`, `src/test_signal.py`, `src/test_volatility.py`, `src/test_volume.py`) — marked deprecated (broken flat imports; the real suite lives in `tests/`). TODO(v1.1): delete
- Duplicate module docstring in `src/__init__.py`

---

## [v1.0.0-rc1] — 2026-07-30

### Added

#### Streamlit Frontend (24 Pages)
- **Dashboard** — Live NEPSE index, top gainers/losers/turnover, market breadth, portfolio value, watchlist signals, recent trades, alerts feed
- **Market Scanner** — Filter by signal, score, confidence, RSI, volume, price, regime; saved presets, export CSV, pinned symbols
- **Stock Analysis** — Deep technical analysis: candlestick, volume, RSI, MACD, Bollinger Bands, ATR, OBV charts; support/resistance levels
- **Advanced Charts** — Real-time WebSocket updates, live candlesticks, live indicators, LIVE/DISCONNECTED status indicator
- **Watchlist** — Live price and signal updates every 30s; add/remove/scan
- **Portfolio** — SQLite-backed persistent portfolio: cash, holdings, transactions, P&L, allocation pie chart, performance
- **Portfolio Analytics** — Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor, rolling returns/Sharpe/volatility
- **Paper Trading** — Market/limit/stop-loss/take-profit orders, pending queue, trade history, open positions, balance tracking
- **Market Regime** — Detected regime with confidence, reasons, metrics (ADX, ATR, RSI, MACD, trend, drawdown, volume)
- **Backtesting** — Select strategy, stocks, date range, parameters; equity curve, drawdown, trades, metrics
- **Optimizer** — Parameter optimisation with heatmap and ranking
- **Alerts Center** — Price, volume, RSI, MACD cross, breakout, regime change, portfolio alerts; read/unread, dismiss, priorities
- **Reports** — Generate CSV, Excel, JSON, HTML, PDF reports for portfolio, backtests, market scan, watchlist
- **Cache & Performance Debug** — Memory/disk cache inspection, metrics, provider health, WebSocket status, background refresh, rate limiter
- **System Status** — Cache status, provider health, API latency, WebSocket, background refresh, memory/CPU, request metrics
- **Notifications** — Unified drawer with unread badge, categories (portfolio, scanner, price, market, WebSocket, system), search, filter
- **Settings** — Theme (Dark/Light/TradingView/Bloomberg), refresh interval, cache TTL, API provider priority, notifications, chart preferences
- **Keyboard Shortcuts** — Ctrl+K/R/P/S/B/D/H/? — global navigation and help

#### DataService (Centralised Data Layer)
- **DataService Singleton** — Single entry point for all market data across the entire application
- **TieredCache** — In-memory (fast) + disk (persistent) with configurable TTL expiry
- **HybridProvider** — Automatic fallback: API → CSV → cache → graceful empty
- **APIProvider** — Configurable REST endpoints with requests/httpx fallback, timeout, error handling
- **CSVProvider** — Automatic discovery of `data/history/*.csv`, `data/stocks/*.csv`, `data/*.csv`
- **RateLimiter** — Token-bucket per provider, exponential backoff with jitter, 429/503 handling
- **ProviderHealthMonitor** — Auto-disable providers after 5 consecutive failures, re-enable after recovery period
- **MetricsCollector** — Total requests, cache hit rate, P50/P95/P99 latency, per-operation and per-provider breakdown
- **LiveMarketStream** — WebSocket with auto-reconnect, heartbeat, subscriber pattern, fallback to polling
- **Background Refresh** — Thread-safe, pause/resume, dynamic interval, graceful shutdown, automatic exception recovery

#### Portfolio & Trading
- **Portfolio Database** — SQLite-backed persistent storage: holdings, transactions, realised/unrealised P&L
- **Portfolio Analytics** — Daily/weekly/monthly/YTD returns, CAGR, Sharpe/Sortino/Calmar, Alpha/Beta, Information/Treynor ratios
- **Paper Trading Engine** — Market/limit/stop-loss/take-profit orders, pending queue, partial fills, commission, trade history
- **Trade Journal** — Automatic logging, notes, screenshots, lessons learned, tags, strategy, confidence, emotion, R multiple
- **Market Replay** — Play/pause/step/fast-forward/rewind through historical sessions; cache injection for live UI compatibility

#### Security & Deployment
- **Security Audit** — Removed traceback leaks (`st.exception`), added CORS middleware (configurable origins), environment variable validation
- **Docker Support** — Multi-stage Dockerfile (web/api/bot targets), docker-compose.yml with health checks
- **CI/CD** — GitHub Actions: test suite + Docker build + multi-arch release pipeline
- **Backup/Restore** — Linux shell scripts and Windows batch scripts with 30-day retention
- **Launcher Scripts** — Linux `run_app.sh` and Windows `run_app.bat` with mode selection (web/api/bot/all)
- **Production Config** — `src/config/production.py` with safe defaults (longer TTLs, conservative rate limits)

### Changed
- **Market Regime Detector**: Added PANIC regime detection, refined DISTRIBUTION and RECOVERY thresholds, updated regime decision order (PANIC → OVERHEATED → BULL → BEAR → RECOVERY → DISTRIBUTION → ACCUMULATION → SIDEWAYS → LOW_VOLATILITY → HIGH_VOLATILITY)
- **Architecture Migration**: All Streamlit pages migrated from scattered `requests.get()` / `pd.read_csv()` calls to centralised DataService
- **Code Quality**: Removed dead code, unused imports, oversized functions; added comprehensive type hints, docstrings, and logging
- **Performance Optimisation**: LRU-cached Plotly figures (`lru_cache`), `@st.cache_resource` for DataService, lazy imports, background data preloading
- **PDF Export**: Added ReportLab-based PDF generation with cover page, tables, charts, page numbers, timestamps

### Fixed
- **Sprint 7A Stability**: Fixed DataService singleton attribute initialization order, removed all `hasattr()` workarounds, fixed provider contract completeness
- **Sprint 7B Test Failures**: Fixed `TopMovers` import error (was referenced before import), fixed `colorscale` property error on Scatter trace in optimisation chart
- **Sprint 7F Security**: Replaced `st.exception(e)` traceback leak with logged error and user-friendly message; added `CORSMiddleware` with configurable origins
- **Sprint 7D Caching**: Fixed `TypeError: unhashable type: 'DataFrame'` in `_build_candlestick` by extracting OHLCV tuples before cache entry

### Performance
- **Dashboard Load**: < 500ms initial load (pre-cached via background refresh)
- **Cached Quotes**: < 20ms (in-memory cache hit)
- **History Load**: < 100ms (tiered cache with disk fallback)
- **Chart Rendering**: ~0ms for repeated inputs (LRU-cached Plotly figures)
- **Cache Hit Rate**: Typically > 80% during active usage

---

## [v0.6.5] — 2026-07-29

### Added
- Complete Streamlit frontend with 24 pages
- DataService singleton with tiered caching
- Hybrid API/CSV/fallback provider chain
- WebSocket live feed infrastructure
- Provider health monitoring and rate limiting
- Portfolio database (SQLite) and analytics
- Paper trading engine (market/limit/stop-loss/take-profit)
- Market replay engine with cache injection
- Alert center with notification drawer
- Theme manager (Dark/Light/TradingView/Bloomberg)
- Keyboard shortcuts (Ctrl+K/R/P/S/B/D/H/?)
- Export center (CSV/Excel/JSON/HTML/PDF)
- Docker deployment (multi-stage, docker-compose)
- CI/CD pipeline (GitHub Actions, GHCR)
- Backup/restore scripts (Linux + Windows)
- Performance debug dashboard
- System status page

### Fixed
- Import errors across Streamlit pages
- Singleton lifecycle management
- Background refresh thread safety
- Provider fallback edge cases
- File encoding issues (utf-8 vs cp1252)
- Plotly version compatibility

---

## [v0.5.1] — 2026-07-25

### Added
- **Backtest Engine Completed**: Finished orchestration layer in `src/backtest/engine.py` connecting CSV data loading, signal evaluation, trade simulation, analytics calculations, and report generation.
- **Backtest Metrics**: Implemented pure quantitative performance metrics in `src/backtest/metrics.py` including win rate, profit factor, average win/loss, expectancy, max drawdown, Sharpe ratio, and CAGR.
- **Report Generator**: Added `src/backtest/report.py` to format trade execution histories and performance statistics into structured summary dictionaries and human-readable text overviews.
- **BACKTESTING.md Documentation**: Created comprehensive technical architecture and usage documentation in `docs/BACKTESTING.md`.
- **55 Automated Tests**: Expanded test suite to 55 comprehensive unit and integration tests with 100% pass rate across engine, simulator, metrics, report, API, and strategy modules.

### Changed & Improved
- **Trade Simulator Refactor**: Refactored `src/backtest/trade_simulator.py` into a professional trade execution engine featuring `ExitReason` and `TradeResult` Enums, `ExecutionConfig` dataclass, normalized `TradeRecord` modeling, position sizing (`shares`), and adverse slippage/commission calculations.
- **API Validation Improvements**: Enhanced input validation across all FastAPI endpoints in `src/api/` (`analyze`, `backtest`, `watchlist`), blocking path traversal attempts (`..`, `/`, `\\`) and bad parameter inputs with appropriate `400 Bad Request`, `404 Not Found`, and `500 Internal Server Error` HTTP status codes.
- **Ranking Engine Improvements**: Refined signal scoring, indicator weighting, and candidate ranking calculations in indicator and scoring modules.
- **Scanner Integration**: Enhanced market scanner services and endpoints (`/market/`, `/market/top10`, `/market/buylist`, `/market/selllist`, `/market/strongbuy`) for real-time candidate screening.
- **Logging Improvements**: Replaced console `print()` statements across API and engine modules with structured `logger` calls (`logger.info()`, `logger.debug()`, `logger.error()`).
- **Documentation Updates**: Updated module docstrings, type annotations, and system architecture docs.

---

## [v0.5.0] — 2026-07-20

### Added
- Portfolio analyzer module (`src/portfolio/analyzer.py`) and advice generator (`src/portfolio/advisor.py`).
- Market scanner service (`src/services/market_service.py`) for automated screening.
- Watchlist manager and scanner (`src/watchlist/manager.py`, `src/watchlist/scanner.py`).

### Changed
- Refactored REST API routers under `src/api/`.

---

## [v0.4.0] — 2026-07-10

### Added
- Core technical analysis engine (`src/engine/analyzer.py`).
- Indicator calculation libraries for Moving Averages, RSI, MACD, Volume, and Volatility (`src/indicators/`).
- Candlestick pattern detection algorithms (`src/patterns/candlestick.py`).
- Signal scoring engine (`src/signals/scorer.py`).
- Market regime detector (`src/regime/detector.py`) with PANIC, OVERHEATED, BULL, BEAR, RECOVERY, DISTRIBUTION, ACCUMULATION, SIDEWAYS regimes.
- Adaptive strategy engine (`src/adaptive/engine.py`).
- Risk management (`src/risk/`): position sizing, reward calculation, engine.
- Decision engine (`src/decision/`): confidence, market filter, risk, signal, support/resistance.
- Monte Carlo simulation (`src/simulation/monte_carlo.py`).
- Parameter optimisation (`src/optimization/parameter_optimizer.py`) and walk-forward analysis.
- Portfolio optimiser (`src/portfolio/optimizer.py`).
- Trade recommendation engine (`src/recommendations/`, including trade plan).
- FastAPI REST endpoints (`src/api/`): analyze, backtest, portfolio, regime, scanner, simulation, watchlist.
- Telegram bot (`src/bot/telegram_bot.py`) for optional monitoring.
- Dashboard charts module (`src/dashboard/charts.py`) and metrics.
- Data loaders for CSV (`src/loaders/csv_loader.py`).
- Market cache (`src/cache/market_cache.py`).
- Data validator (`src/validators/data_validator.py`).
- Comprehensive test suite with 800+ tests.

### Changed
- Project restructuring into modular `src/` package layout.
- Migration from flat scripts to clean architecture.
