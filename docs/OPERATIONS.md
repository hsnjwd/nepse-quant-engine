# Operations Runbook

Sprint 13.9 §22 — concise, *executable* procedures for operating the
NEPSE Quant Engine.  Every procedure states **symptom → check →
action → expected result → escalation condition**.  Everything here
runs against the real system — no theoretical steps.

Related: [STATE_INVENTORY.md](STATE_INVENTORY.md),
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md), [DEPLOYMENT.md](DEPLOYMENT.md).

## Service topology

| Service | Entrypoint | Port | Health probe |
|---|---|---|---|
| Streamlit frontend (`web`) | `python -m streamlit run app.py` | 8501 | `/_stcore/health` |
| FastAPI backend (`api`) | `python -m uvicorn src.api.main:app` | 8000 | `/health/live` (liveness), `/health/ready` (readiness) |
| Telegram bot (`bot`, optional) | `python -m src.bot.telegram_bot` | — | — |

Docker: `docker compose up -d web` / `docker compose up -d api` /
`docker compose --profile bot up -d`.  Native: see
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## 1. Normal startup

- **Symptom:** deploying a new instance / restarting the platform.
- **Check:** `git status` clean on the release branch; VERSION matches the
  changelog; `data/state/nepse_calendar.json` present.
- **Action:**
  ```bash
  docker compose up -d --build web api
  docker compose ps          # both services should show healthy
  curl -sf http://localhost:8000/health/live && echo live
  curl -sf http://localhost:8000/health/ready && echo ready
  ```
- **Expected result:** both healthchecks green; `/` returns
  `{"status": "NEPSE Quant Engine Running"}`.
- **Escalation:** `/health/ready` returns 503 — see State corruption /
  Invalid configuration below.

## 2. Normal shutdown

- **Symptom:** planned maintenance / upgrade window.
- **Check:** nothing — shutdown is safe at any time (atomic writes,
  cross-process locks with stale-breaking, daemon threads).
- **Action:** `docker compose down` (or `kill -TERM <api-pid>` on native).
- **Expected result:** containers exit cleanly; no leftover `.tmp`/`.lock`
  files under `data/`; state files remain valid JSON (validate:
  `python3 scripts/check_json_files.py`).
- **Escalation:** process does not exit within ~30 s — `kill -KILL` (state
  stays safe: writes are atomic; a stale lock file is broken on next
  start within 8 s).

## 3. Provider failure

- **Symptom:** market data stops updating; `/metrics` shows
  `system_status.data_provider = DEGRADED/UNAVAILABLE` or
  `incidents.counts.provider_unavailable` rising.
- **Check:** `curl -s localhost:8000/metrics | python3 -m json.tool` —
  read `provider_health` and `system_status`.
- **Action:** nothing is required — the hybrid provider chain falls back
  (API → CSV).  If the fallback is also failing, verify
  `DATA_DIRECTORY` contains CSV data and network egress is available for
  `NEPSE_SCRAPER_URL` / `NEPSE_CLIENT_URL`.
- **Expected result:** analysis continues via fallback; alerts stay safe
  (data is classified untrusted before it can produce a signal).
- **Escalation:** provider unavailable > 24 h — file an incident; do NOT
  restart the process for this (an external outage is not a process
  failure; liveness must stay green).

## 4. Provider recovery

- **Symptom:** `provider_health` recovers to enabled/healthy.
- **Check:** `/metrics` → `provider_health` per-provider state.
- **Action:** none — the health monitor re-enables providers per its
  recovery policy (success-count based).
- **Expected result:** `system_status.data_provider = HEALTHY`; fresh data
  flows again.
- **Escalation:** provider flaps (recover → fail repeatedly) — investigate
  upstream; consider `SECOND_PROVIDER_URL` only after validating the
  source (Sprint 13.6 policy).

## 5. Data-quality degradation

- **Symptom:** `/metrics` → `system_status.data_quality = DEGRADED`
  (invalid records / conflicts) or `data_quality_trend` worsening.
- **Check:** inspect the counters (`records_invalid`, `conflicts`).
- **Action:** identify the offending symbol(s) via analysis endpoints; fix
  or quarantine bad CSV rows; re-run `python3 scripts/verify_data_pipeline.py`.
- **Expected result:** quality counters stop rising; degraded data is
  suppressed from signals, never trusted.
- **Escalation:** wholesale degradation (every symbol) — check the corpus
  refresh and calendar; restart the corpus repair
  (`python3 scripts/repair_corpus.py`).

## 6. Calendar update

- **Symptom:** a new holiday / special session must be added.
- **Check:** current version: `curl -s localhost:8000/metrics | grep calendar`.
- **Action:** use the governed update path (validates schema, conflicts,
  provenance, regression, and backs up the previous calendar before
  activation).  CLI equivalent: submit via the calendar API/UI; or edit
  `data/state/nepse_calendar.json` through the validated update function.
- **Expected result:** new calendar active with version bump; history
  recorded (`data/state/nepse_calendar_history.json`); `/health/ready`
  still 200.
- **Escalation:** update rejected — read the validation problems from the
  response and correct the candidate; never force-activate.

## 7. Calendar rollback

- **Symptom:** a calendar change produces wrong trading days
  (`missing_sessions` spikes in `/metrics`).
- **Check:** `curl -s localhost:8000/metrics | grep -A5 calendar`.
- **Action:**
  ```bash
  python3 -c "from src.data.calendar import rollback_calendar; print(rollback_calendar())"
  ```
  The rollback candidate is re-validated; a corrupt backup is rejected
  and the active calendar left untouched; the pre-rollback calendar is
  itself preserved.
- **Expected result:** previous valid calendar restored; version history
  shows the rollback.
- **Escalation:** no backup available — restore
  `data/state/nepse_calendar.json` from `scripts/backup.sh` output and
  restart.

## 8. Cache corruption

- **Symptom:** analysis slow / `system_status.cache = DEGRADED`.
- **Check:** cache round-trip probe in `/metrics` →
  `system_status.cache`.
- **Action:** `rm -rf ~/.nepse/cache` (disk cache is disposable; a corrupt
  entry is a miss, never a crash).
- **Expected result:** cache rebuilds on demand; no signal ever originates
  from a corrupt cache (cache entries are revalidated on read).
- **Escalation:** cache keeps failing — check disk space / permissions.

## 9. State corruption (calendar / watchlist / alerts / metrics)

- **Symptom:** `/health/ready` 503; loaders fall back to defaults; log
  lines mention `*.corrupt.bak`.
- **Check:** find the evidence: `find data -name '*.corrupt.bak'`;
  validate all JSON: `python3 scripts/check_json_files.py`.
- **Action:** inspect the `.corrupt.bak` file; if the state is genuinely
  lost, the store has already fallen back to its safe default.  If the
  data matters, restore from backup (below).  Then re-run the checker.
- **Expected result:** the engine keeps operating on the safe default;
  corrupted state is never invented and never trusted.
- **Escalation:** calendar unavailable → base weekend rule is used
  (`calendar_status = DEGRADED`) — still safe; if that also fails,
  restart after restoring the shipped calendar.

## 10. Worker failure (two-worker deployment)

- **Symptom:** `docker compose ps` shows `api` restarting; `/metrics`
  aggregate worker count drops.
- **Check:** `curl -s localhost:8000/metrics | grep -A20 aggregate` —
  active worker records within `WORKER_METRICS_TTL_S`.
- **Action:** nothing — the other worker keeps serving; the dead worker's
  state (alert history, watchlist, worker metrics) is protected by
  cross-process locks and atomic writes; `restart: unless-stopped`
  respawns it.
- **Expected result:** worker count returns to 2; no lost updates; no
  corrupt state.
- **Escalation:** both workers fail — treat as full restart (below).

## 11. Full restart

- **Symptom:** after machine restart / deploy / both workers down.
- **Check:** state files valid (see §9); volumes mounted
  (`docker compose ps`).
- **Action:** `docker compose up -d --build web api` (or
  `docker compose restart`).
- **Expected result:** `/health/live` and `/health/ready` green; state
  loads; provider health resets per policy; alerts remain safe.
- **Escalation:** readiness 503 after 60 s — follow §9 / §12.

## 12. Upgrade

- **Symptom:** deploying release N+1.
- **Check:** `python3 scripts/verify_release_artifact.py dist/*.tar.gz`
  clean; release manifest present
  (`data/state/release_manifest.json`) and verified
  (`python3 scripts/build_release_manifest.py --verify`); calendar
  schema supported (`SUPPORTED_CALENDAR_VERSIONS`).
- **Action:** back up state first (`sh scripts/backup.sh`), then deploy
  the artifact per [DEPLOYMENT.md](DEPLOYMENT.md), then run the §1
  startup checks.
- **Expected result:** old state loads (versioned stores migrate
  explicitly; incompatible state fails safe); all healthchecks green.
- **Escalation:** migration error — do NOT delete or reinterpret the old
  state; roll back (§13).

## 13. Rollback

- **Symptom:** N+1 misbehaves in production.
- **Check:** release artifact for N available; state backup exists.
- **Action:** stop N+1, redeploy N, restore the state backup taken before
  the upgrade, restart, run §1 checks.
- **Expected result:** N starts; state readable; calendar valid; no
  corrupted migration remains; trust state stays safe.
- **Escalation:** rollback also fails — restore `data/` from the latest
  `backup.sh` archive and investigate with the state evidence preserved
  (`*.corrupt.bak` files).

## 14. Incident investigation

- **Symptom:** anything above, or an alert from monitoring.
- **Check (in order):**
  1. `/health/live` vs `/health/ready` — process vs capability.
  2. `/metrics` — `system_status` blocks, `provider_health`, `incidents`,
     `data_quality_trend`, `calendar`, `api_requests` (p99 latency /
     error counts), `process` (pid/hostname per worker).
  3. `docker compose logs --tail=200 api web`.
  4. State evidence: `find data -name '*.corrupt.bak' -o -name '*.tmp'`.
- **Action:** classify (process / config / data / provider / calendar),
  apply the matching procedure above.
- **Expected result:** root cause identified; the trust chain
  (`bad data → detected → classified → HOLD`) held — no unsafe signal or
  alert was produced.
- **Escalation:** cannot classify within the incident SLA — restore from
  backup (§13) and file the evidence.

---

## Monitoring cheat-sheet

| Question | Endpoint / field |
|---|---|
| Is the system alive? | `GET /health/live` |
| Is it ready? | `GET /health/ready` |
| Providers healthy? | `/metrics` → `provider_health`, `system_status.data_provider` |
| Fallback active? | `system_status.data_provider.reason` (disabled list) |
| Conflicts occurring? | `system_status.reconciliation`, `incidents.counts` |
| Data quality degrading? | `/metrics` → `data_quality_trend` |
| Calendar valid? | `/metrics` → `calendar_status`, `system_status.calendar` |
| Incidents increasing? | `/metrics` → `incidents.counts` |
| System recovering? | `/metrics` → `system_status.overall` over time |

Metrics are bounded by design (fixed-size snapshots, TTL-pruned worker
records, capped incident/quality counters) and expose no secrets.
