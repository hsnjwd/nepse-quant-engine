# Production Readiness — NEPSE Quant Engine

Sprint 13.0 — an end-to-end validation of the engine as one integrated
production system.  This document records the architecture actually
validated, the validation matrix, verified performance baselines,
failure-handling behaviour, persistence contracts, deployment paths,
security findings, and the readiness decision.

> Evidence was produced by **executing the integrated application**, not
> by unit-test greenness alone.  The single consolidated command is:

```bash
python -m benchmarks.production_gate --synthetic 12 --real 12
```

All validation is **local** (this machine).  No claim of external /
cloud production operation is made.

---

## 1. Architecture

The data flow validated end-to-end (real production functions, no mocks):

```
NEPSE data (CSV corpus, synthetic + real shipped corpus)
    ↓
DataService (src/data/service.py — singleton, tiered cache, hybrid provider)
    ↓
Provider: API (dead-port disabled in gate) → CSV fallback (src/data/providers.py)
    ↓
Scanner (src/scanner/engine.py — ThreadPoolExecutor + scanner cache)
    ↓
Indicators (src/indicators/* — single-copy in-place pipeline + indicator cache)
    ↓
Analyzer (src/engine/analyzer.py — analyze_stock / analyze_dataframe)
    ↓
Signal scoring (src/signals/scorer.py) + confidence + trend + S/R
    ↓
Ranking (src/scanner/ranking.py — deterministic stable sort)
    ↓
Alerts (src/alerts/engine.py — RLock + cross-process lock + atomic JSON)
    ↓
Watchlist / Portfolio (src/watchlist/*, src/portfolio/*)
    ↓
FastAPI (src/api/main.py — uvicorn, request-timing middleware, /metrics)
    ↓
Streamlit (app.py + src/ui/pages/*)
```

Components **exercised** by the gate:

| Layer | How exercised |
|---|---|
| DataService | import smoke tests (`import app`, `import src.api.main`), API requests through the real server, CSV provider fallback |
| Scanner | `scan_market` over synthetic + real corpora (core_pipeline, recovery) |
| Indicators | through `analyze_stock`/`analyze_dataframe`; indicator-cache counters via `/metrics` |
| Analyzer | `/api/analyze/{symbol}` cold/warm/repeated; `analyze_stock` in ci_gate |
| Signals/Confidence/Ranking | in-process scan + `rank_market` determinism check |
| Alerts | `process_alert_batch`, concurrent writers, corrupt-history recovery |
| Watchlist/Portfolio | real CRUD endpoints, `/api/portfolio`, concurrent writes |
| FastAPI | real uvicorn single-worker and `--workers=2` |
| Streamlit | real headless boot, `/_stcore/health`, Metrics page HTTP fetch |

Components **not exercised** (documented limitation): the live NEPSE
API provider (the gate pins provider URLs to a dead loopback port so
every run is hermetic and deterministic — the *CSV fallback* path that
production uses when the API is unavailable is what is exercised);
Docker containers (the Docker CLI was unavailable on this machine, so
only the `uvicorn src.api.main:app --workers=N` deployment semantics —
the Docker api stage — were validated directly, exactly as
`benchmarks.validate_workers` does).

---

## 2. Validation matrix

Run via `python -m benchmarks.production_gate` (Sprint 13.0), 12
sections, plus the pre-existing gates.

### Consolidated gate (12 sections)

| Section | Status | What was validated |
|---|---|---|
| core_pipeline | PASS | In-process scan over 12 synthetic + 12 real symbols; required fields present; `rank_market` deterministic; `analyze_stock` attaches `new_alerts`; real-corpus leg analysed; zero skipped |
| persistence | PASS | 8 concurrent `update_json` writers — no lost update, no `.tmp`/`.lock` litter; corrupt JSON backed up as `.corrupt.bak` and default returned |
| performance | PASS | `ci_gate` all 6 checks (cold_load, warm_speedup, warm_scan, warm_api, warm_portfolio, warm_api_p99); `SCANNER_CACHE_MAX_ENTRIES` (600) ≥ real corpus usable count |
| api_live | PASS | Real single-worker uvicorn; `/`, `/metrics`, `/analyze/{symbol}` cold→warm→repeat stable, `/watchlist` CRUD + scan, `/portfolio`, `/market/top10`; invalid/traversal/unknown inputs → 4xx |
| cache | PASS | Cold/warm equivalence on 13 stable fields; scanner dataframe tier + indicator cache hits > 0 via `/metrics` counters (analysis tier is exercised by scan_market in core_pipeline) |
| multi_worker | PASS | `uvicorn --workers=2`; 8 concurrent requests all 200; `/metrics` aggregate ≥ local counters (sum semantics); worker pid(s) observed (≥1 — loopback may route to one worker, noted not failed).  Gate infrastructure fix this sprint: sections now tear down their *own* server in `finally` (`_close_section`), so each section measures on a quiet machine — in-gate the 8-way barrage once timed out (4/8) under 3+ co-resident gate servers while passing 8/8 standalone (see the Sprint 13 completion report, Issues section) |
| concurrency | PASS | 5- and 10-concurrent-request smokes across `/analyze` + `/metrics`; 100% success; latency p50/p95/p99 recorded |
| streamlit | PASS | Real Streamlit app boots headless; `/_stcore/health` answers; Metrics page `fetch_metrics` against the live backend reports ≥1 worker |
| bot | PASS | Real `src.bot.telegram_bot` boots in a fresh subprocess against the live API; the real `Application` is built with the injected `TELEGRAM_TOKEN` (dummy when unset — noted); all 13 handler checks (`/start`, `/help`, `/analyze`, `/backtest`, `/signals`, `/top10`, `/buylist`, `/selllist`, `/strongbuy`, `/market`, `/watchlist`, `/watchlist scan`, `/portfolio`) invoked and verified via their real HTTP calls; `/start` help text checked; `buy_zone` → `entry_zone` /analyze fix (stale-field bug) and the six added commands covered by regression tests. Live Telegram polling not exercised (needs a real token + network) — documented, never hidden |
| recovery | PASS | Corrupt CSV isolated as `skipped` without breaking healthy symbols; API starts and serves `/` with a corrupt `history.json`; API restart (kill + respawn) recovers, `/analyze` healthy |
| security | PASS | AST scan of `src`: zero unsafe `pickle.load`, zero `eval`/`exec`, zero hardcoded secrets; live `/metrics` payload leaks no paths/secrets |
| startup_deploy | PASS | Fresh-subprocess `import src.api.main` and `import app` (private cwd/env) complete; docker config SKIPPED (CLI unavailable) — noted, not hidden |

Final verdict: **12 PASS / 0 FAIL / 0 CONDITIONAL / 0 SKIPPED** →
**PRODUCTION READY**.

### Pre-existing gates (re-run for Sprint 13, Phase 11)

| Gate | Command | Result |
|---|---|---|
| CI benchmark regression gate | `python -m benchmarks.ci_gate` | PASS — all 6 checks; warm `/api/analyze` p99 ~96 ms (limit 2000 ms) |
| Worker validation | `python -m benchmarks.validate_workers` | PASS (Sprint 11.6–12.x, unchanged code) |
| Full unit suite | `pytest` | see §3 |

---

## 3. Performance baselines (verified this sprint)

| Metric | Measured | Bound |
|---|---|---|
| Scanner cold load (20×300 synthetic, best-of-3) | ~160–400 ms | baseline × 3.0 |
| Scanner warm load speedup | ~10x+ | ≥ 5x required |
| Warm scan vs cold scan ratio | ~0.05 (documented reference; artifact `scanner_warm_cold_ratio`) | ≤ 0.5 |
| Warm `/api/analyze` vs cold ratio | ~0.44 | ≤ 0.75 |
| Warm `/api/portfolio` vs cold ratio | ~0.44 | ≤ 0.75 |
| Warm `/api/analyze` per-symbol p99 | ~96 ms | ≤ 2000 ms |
| Warm scanner per-symbol cost (Sprint 12.3) | ~0.6 ms | — |

Cache-capacity guarantee (Sprint 12.3 finding): `SCANNER_CACHE_MAX_ENTRIES`
default 600 ≥ shipped real corpus usable count (~280), so warm scans
never silently re-parse the corpus.  Verified in the performance section.

---

## 4. Failure handling (expected degraded-mode behaviour)

| Failure | Expected behaviour | Validated |
|---|---|---|
| Missing/corrupt CSV for a symbol | Symbol isolated as `skipped`; healthy symbols still analysed | recovery PASS (BROKEN.csv → skipped, 4 healthy analysed) |
| Malformed `alerts/history.json` | Backed up as `.corrupt.bak`, default returned; API still serves | recovery + persistence PASS |
| Provider unavailable | API provider fails fast (dead port → connection refused); CSV fallback serves | api/cache sections run fully offline |
| Unknown symbol | `/analyze` → 404 | api_live PASS |
| Path-traversal input | `/analyze/..%2Fetc` → 400/404, never 500 | api_live PASS |
| API restart | Kill + respawn returns to healthy state; `/analyze` serves | recovery PASS |
| Streamlit without backend | `fetch_metrics` raises; page must not crash (Sprint 12.1 coverage) | unit-tested in `test_sprint12_1.py` |

---

## 5. Persistence

| State file | Path | Mechanism |
|---|---|---|
| Alert history | `data/alerts/history.json` | atomic write (temp + rename), cross-process lock (`locked_json`), corrupt-backup recovery |
| Portfolio | `portfolio.json` | `update_json` atomic transaction |
| Watchlist | `data/watchlist/watchlist.json` | atomic + RLock; insertion-order semantics |
| Worker metrics | `data/state/worker_metrics.json` | cross-worker self-report, TTL liveness, best-effort |

Validated: 8-thread concurrent writes lose no updates; no `.tmp`,
`.lock` or `.corrupt.bak` litter; interrupted/corrupt files recover to
the documented default; watchlist insertion-order semantics (add,
duplicate-add no-move, remove frees slot, re-add appends) are covered
by `validate_workers` Phase 15.

---

## 6. Deployment

| Path | Command | Status |
|---|---|---|
| API (single worker) | `uvicorn src.api.main:app --host 127.0.0.1 --port N` | validated live |
| API (Docker api stage) | `uvicorn src.api.main:app --workers=2` | validated live (multi_worker section) |
| Streamlit | `streamlit run app.py` (headless) | validated live |
| Docker | `docker compose config` | SKIPPED — Docker CLI unavailable locally; deployment semantics validated directly via the equivalent uvicorn command |

Required configuration: `DATA_DIRECTORY` (CSV corpus).  Optional:
`NEPSE_HOME`, `DATA_SERVICE_API_URLS` env keys, scanner/cache TTLs,
`WORKER_METRICS_FILE`, `METRICS_FETCH_TTL_S`.  The gate runs every
server with a private `cwd` + `DATA_DIRECTORY` + `NEPSE_HOME`, so
production state files are never read or written (state-isolation
snapshot of the four protected files is part of the gate verdict).

---

## 7. Security findings

| Finding | Severity | Status |
|---|---|---|
| `src/ml/model_manager.py` loaded model artifacts with raw `pickle.load` (arbitrary code execution on a tampered artifact) | **CRITICAL** | **FIXED** — `_RestrictedUnpickler` allowlists `src.ml.*`, numpy, sklearn, scipy, lightgbm, xgboost + curated safe stdlib modules + safe builtins (blocks `eval`/`exec`/`open`/`getattr` gadgets); path-containment guard refuses artifacts outside the manager directory.  Residual risk documented in the module docstring (broad numpy/scipy prefixes remain a theoretical surface for crafted artifacts; artifacts are only loaded from the manager's own directory). |
| Hardcoded secrets / tokens in `src` | — | None found (AST scan clean) |
| `eval`/`exec` in `src` | — | None found |
| `/metrics` leaking paths/secrets | — | None found (live payload check clean) |

Residual (documented): the model-loading allowlist breadth is a
LOW/MEDIUM residual risk; a fully hardened loader would deny-list known
library primitives (`numpy.load`, `numpy.ctypeslib.load_library`).

---

## 8. Limitations

- Live NEPSE API integration was **not** exercised — provider URLs are
  pinned to a dead loopback port so the gate is hermetic; the CSV
  fallback (the degraded mode production uses when the API is down) is
  what runs.
- Docker build/compose-up was **not** run (Docker CLI unavailable); the
  deployment semantics of the Docker api stage (`uvicorn --workers=2`)
  were validated directly.
- No 2–4 hour long-running stability soak was run; the stability
  harness is built into the gate (`--stability-min N`) and defaults to
  off for normal runs.
- Streamlit rerun/TTL behaviour is unit-covered (Sprint 12.1) rather
  than re-verified live in this sprint.
- All validation is local; no claim of cloud/production-host validation.

---

## 9. Running the gate

```bash
# Full consolidated readiness gate (~5-6 min, hermetic, offline)
python -m benchmarks.production_gate --synthetic 12 --real 12

# Include a controlled stability soak (opt-in)
python -m benchmarks.production_gate --stability-min 10

# Artifact
#   benchmarks/results/production_gate.json
```

The gate exits 0 only on `PRODUCTION READY`; the verdict distinguishes
`PASS` / `FAIL` / `SKIPPED` / `CONDITIONAL` per section and never marks
a section PASS that was not executed.

---

## 10. Sprint 13.7 — Production Readiness Audit & Operational Safety

Sprint 13.7 re-audited the whole trading workflow with the trust,
calendar, reconciliation and provenance layers now in the path:

```
Provider → identity → reconciliation → calendar → quality
→ provenance/trust → cache → analyzer → scanner → alerts → notification
→ API / Streamlit / Telegram
```

The governing guarantee under test (2,949 passed / 2 skipped full
suite, including the 120-test Sprint 13.7 file, all green):

> No provider failure, data-quality failure, reconciliation conflict,
> calendar error, stale condition, cache corruption, worker failure, or
> operational degradation may silently result in an unsafe trading
> signal or alert.

### Subsystem classification

| Subsystem | Classification | Evidence |
|---|---|---|
| Provider chain + fallback | **READY** | hybrid chain, per-provider health, timeout path, all-providers-fail → `DataUnavailable`; controlled file-backed second provider double in tests |
| Identity / symbol validation | **READY** | path-traversal rejected (400), unknown symbol → 404, duplicate-source dedup |
| Cross-provider reconciliation | **READY** | AGREE / MINOR / MATERIAL / MAPPING classification; conflicted outcomes dropped, never averaged, never cached as trusted |
| Trading calendar | **READY** | governed, versioned calendar; weekend (Fri/Sat) vs holiday vs missing-session classification; validated holiday-intake workflow with version bump + rollback |
| Data quality | **READY** | centralized validator; invalid OHLC quarantined; quarantined symbols skipped, never silently signal |
| Provenance / trust | **READY** | single canonical resolution; worst-state-wins; `is_safe` on every dataset; auto-derived on scanner/CSV paths |
| Cache | **READY** | provenance-aware entries, LRU bound ≥ corpus, TTL invalidation, reconciled entries never cached when conflicted |
| Analyzer | **READY** | unsafe trust states suppress to HOLD with explicit reason; rule alerts scrubbed on suppressed analyses |
| Scanner | **READY** | parallel + cached; per-file isolation; skips carry explicit reasons; no duplicate trust-resolution logic |
| Alerts | **READY** | RLock + cross-process lock + atomic writes; suppressed analyses emit SUPPRESSED only, never BUY/SELL; single-read/single-write batch |
| Notification centre | **READY** | isolated state; suppressed signals never escalate to actionable notifications |
| FastAPI + multi-worker | **READY** | real 2-worker uvicorn validated: health, metrics, worker identity (≥2 known pids), shared-state integrity, restart recovery, 5-way concurrent DataService access |
| Streamlit | **READY** | metrics dashboard + `/metrics` contract; backend-down does not crash the page |
| Telegram bot | **CONDITIONALLY_READY** | all handler paths validated against the live API (13 commands); live Telegram polling requires a real token + network (see dependency register) |
| Docker deployment | **CONDITIONALLY_READY** | Docker CLI unavailable locally; deployment semantics (`uvicorn --workers=2`) validated directly |
| Security posture | **READY** | AST scan: zero secrets, zero `eval`/`exec`; `/metrics` and `/analyze` errors leak no paths/secrets |

### External dependency register

| Dependency | Purpose | Verification label |
|---|---|---|
| Live NEPSE API (`nepseapi.surajrimal.dev/api/v1`) | primary live-quote/summary provider | **NOT VERIFIED** in the gate (pinned to a dead loopback port so every run is hermetic); reachability probed separately — the CSV fallback that production uses during outages is what the gate exercises |
| yonepse mirror (`shubhamnpk.github.io/yonepse/data`) | corpus refresh + market summary source | **LIVE VERIFIED** — end-to-end market-summary fix (index/change/turnover/volume) and `scripts/refresh_corpus.py` shard ingestion validated against the live source |
| Telegram Bot API (`api.telegram.org`) | bot messaging | **NOT VERIFIED** — live polling needs a real token; the full handler surface is verified against the local API instead |
| Docker CLI | container deployment | **NOT VERIFIED** — unavailable locally; api-stage semantics validated via the equivalent `uvicorn --workers=2` command |
| Local CSV corpus (`DATA_DIRECTORY`) | history/scan data | **LIVE VERIFIED** — synthetic + real corpora; corrupt rows quarantined and repaired with independent re-validation (Sprint 13.3–13.4) |

### Verification labels

- **LIVE VERIFIED** — exercised against the real external system or
  corpus on this machine, with recorded evidence.
- **NOT VERIFIED** — deliberately not exercised (hermetic gate / no
  credential / CLI unavailable); the limitation is documented per entry
  and never silently upgraded to a PASS.

The classification scale is **READY** / **CONDITIONALLY_READY** /
**NOT_READY**: no subsystem is **NOT_READY** in this audit (every
unsafe path is either closed or documented with a concrete unblocking
step); the token is defined so the scale stays complete — a subsystem
whose failure path could not be proven safe would be classified
**NOT_READY** rather than being silently upgraded.
- Every subsystem classified **READY** above carries test-level
  verification in `tests/test_sprint13_7.py` (behavioural, hermetic,
  no fabricated live provider results, no fabricated NEPSE holidays).

### Audit outcome

All 20 audit categories pass: no unsafe path exists by which an
upstream failure can silently produce an unsafe signal or alert.
Residual items are the documented **CONDITIONALLY_READY** entries
(live bot polling, Docker CLI) — each has a concrete unblocking step.

## 11. Sprint 13.8 — Sustained Operation & Benchmark Gate Hardening

Sprint 13.8 extends the audit from *correct for a single execution* to
*correct, bounded, recoverable and measurable across sustained operation*:

```
Fetch → Validate → Reconcile → Provenance → Cache → Analyze → Scan
→ Alert → Metrics → Repeat
```

repeatedly experiencing provider failure/recovery, fallback, minor/material
/mapping conflicts, calendar validation, cache hit/miss, worker restart and
notification activity — without state contamination, unbounded memory,
subprocess/file/thread leakage, cache corruption, unsafe signals/alerts, or
benchmark instability caused by the test harness.

### Performance gate: root cause + outcome (thresholds unchanged)

The known flake (`test_pass_case_exits_zero`, cold/warm speedup ≥ 5x) was
root-caused to **two harness defects**, not an engine regression:

1. `ScannerCache._file_fingerprint` ran a `Path.resolve()` syscall per file
   per rep — fixed per-file overhead that inflated the light warm leg
   disproportionately under I/O interference (failed artifacts showed the
   same +27 ms on both legs, exactly the fingerprint cost × 12 files).
   Removed; fingerprints use content + stat data already read.
2. Sequential 5-cold-then-5-warm measurement let a slow machine phase hit
   only one leg.  Legs now use **interleaved cold/warm pairs** with the
   median of per-pair ratios.

Environmental contention is now *measured*, not assumed: the gate probes
wall/CPU ratio and a min-of-3 alert-history I/O micro-benchmark at leg
boundaries and between every pair; a failing pass reruns once only when a
probe exceeds its documented threshold, and the rerun applies the identical
acceptance requirements.  No "retry until lucky": every rerun is justified
by a measured signal and must pass the same gate.

**Measured results (this sprint):**

| Run set | Runs | Speedup (≥5x) | Warm API (≤0.75) | Warm portfolio (≤0.75) | Analyze p99 (≤2000 ms) |
|---|---|---|---|---|---|
| Interleaved-pairing 10x | 10/10 PASS | 14.78–22.33 | 0.590–0.677 | 0.619–0.653 | 86.3–189.8 |
| Final code 3x | 3/3 PASS | 15.54–16.39 | 0.642–0.645 | 0.628–0.644 | 65.8–111.2 |
| Controlled 4-way CPU contention | PASS | 25.58 | 0.747 | 0.683 | 349.5 |

Under contention the environmental signal was recorded (wall/CPU 1.293,
I/O probe 66 ms > 60 ms threshold at the time; threshold since tightened to
45 ms with between-pair probes) and the gate still passed on the first pass.
A side-process I/O burst during a validation run was captured by the
between-pair probes (56.4 ms vs ~25 ms quiet) with no false rerun.

### Soak outcome (measured)

| Metric | Value |
|---|---|
| Iterations | 60 (real hermetic pipeline, offline) |
| Duration | 269 s |
| Scenarios | A normal, B fallback, C conflict, D cache — all PASS |
| Failures | 0 |
| Threads delta | 0 |
| Open file objects delta | 0 |
| Temp/lock/corrupt litter | none |
| Incident events delta | 0 |
| Repeated-minor symbols delta | 0 |
| Quality snapshots delta | 0 |
| Notifications delta | 0 (3 live / 500 cap) |
| Scanner cache | 0 entries retained / 600 cap |
| Memory growth (tracemalloc) | 0.077 MiB (plateau 2.128 → end 2.205 MiB, 8.0 MiB envelope) |

Memory growth **decelerates and plateaus**: end-of-soak traced memory at 60
iterations (2.205 MiB) ≈ 25 iterations (2.152 MiB), and the series shows GC
collections resetting the heap between climbs — cache/allocator warm-up, not
a leak.  The `state_json_bytes_delta` (11.7 KB) is the fixed-size alert
history file reaching its bounded steady state, identical at 25 and 60
iterations (not per-iteration growth).

### Sustained-operation verdict

The engine repeatedly ran the full workflow under every listed stress
without accumulating unsafe state.  Combined regression (Sprint 13.2–13.8 +
performance, 744 tests) passes; full suite **3051 passed, 2 skipped** (3053
collected).  No test removed, no threshold weakened.

**Remaining risks** are unchanged from Sprint 13.7 and environmental, not
code: live Telegram polling and Docker CLI remain CONDITIONALLY_READY
(no credential / CLI unavailable); a second live provider source remains
unvalidated by design (documented as NOT AVAILABLE until an operator
supplies `SECOND_PROVIDER_URL`).  Benchmark harness noise is now measured
and isolated from the gate rather than being invisible.
