# Persistent-State Inventory

Sprint 13.9 §12 — an explicit inventory of every durable store the
NEPSE Quant Engine writes.  The machine-readable source of truth is
`src/utils/state_inventory.py` (`STATE_STORES`); this document is its
human-readable mirror and must stay in sync with it.

All JSON stores route through `src/utils/json_store.py`, which provides
the shared guarantees:

* **Atomic writes** — temp file in the same directory, flushed, then
  `os.replace` (readers never observe a partial file).  Critical,
  low-frequency state (calendar, release manifest) additionally uses
  `fsync=True` (Sprint 13.9 §14).
* **Cross-process locking** — per-file in-process `RLock` + an atomic
  `O_CREAT|O_EXCL` lock file with stale-lock breaking, so two uvicorn
  workers can never lose each other's updates.
* **Corruption tolerance** — a missing file returns the caller's
  default; an empty/malformed file is renamed aside (`*.corrupt.bak`,
  evidence preserved) and the default is returned.  A damaged file is
  never silently overwritten in place and never *invents* state.

| Store | Owner | Location | Format | Versioned | Atomic | Locked | Corruption policy | Backup | Rollback |
|---|---|---|---|---|---|---|---|---|---|
| Trading calendar | `src/data/calendar.py` | `data/state/nepse_calendar.json` | json | yes (1.0 / 2.0) | yes (+fsync) | yes | backup-aside + validated base-weekend default | `*.bak.json` + version history | `rollback_calendar()` |
| Calendar history | `src/data/calendar.py` | `data/state/nepse_calendar_history.json` | json | no | yes | yes | backup-aside + empty | calendar cycle | rebuilt on activation |
| Watchlist | `src/watchlist/manager.py` | `data/watchlist/watchlist.json` | json | no | yes | yes | backup-aside + empty | `backup.sh` | `restore.sh` |
| Alert history | `src/alerts/history.py` | `data/alerts/history.json` | json | no | yes | yes | backup-aside + empty | `backup.sh` | `restore.sh` |
| Worker metrics | `src/utils/worker_metrics.py` | `data/state/worker_metrics.json` | json | no | yes | yes | backup-aside + tolerated | `backup.sh` | regenerated |
| Disk cache | `src/data/cache.py` | `{NEPSE_HOME}/cache` | json | no | yes | no (last-write-wins) | read failure = miss | `backup.sh` | clear cache |
| Trade journal | `src/trading/journal.py` | `{NEPSE_HOME}/journal` | json | no | yes | yes | backup-aside + default | `backup.sh` | `restore.sh` |
| Alert rules | `src/alerts/center.py` | `{NEPSE_HOME}/alerts.json` | json | no | yes | yes | backup-aside + default | `backup.sh` | `restore.sh` |
| Portfolio DB | `src/portfolio/database.py` | `{NEPSE_HOME}/portfolio.db` | sqlite | no | yes (transactions) | yes | journal; corrupt db recreated empty | `backup.sh` | `restore.sh` |
| Market corpus | `src/loaders`, `scripts/refresh_corpus.py` | `data/raw/*.csv` | csv | no | yes | no | record-level validation rejects bad rows | `backup.sh` | `restore.sh` |
| Release manifest | `src/utils/release_manifest.py` | `data/state/release_manifest.json` | json | yes (schema 1.0) | yes (+fsync) | no (build-time) | regenerate | regenerated (no state) | regenerate |

`{NEPSE_HOME}` expands to `$NEPSE_HOME` or `~/.nepse`.

## Corruption behaviour (all stores)

```
corruption
   ↓
detected (json_store or SQLite journal)
   ↓
classified (unreadable / wrong shape / unsupported version)
   ↓
evidence preserved (*.corrupt.bak or journal)
   ↓
safe default returned
   ↓
never invented state, never silently trusted
```

The only store with *silent* tolerance is the disk cache — a cache miss
is a safe default by definition.  The calendar is the strictest: an
unsupported version or invalid payload is rejected outright and the
base weekend rule is used, so a broken calendar can never turn an
exchange closure into a trading day.

## Lifecycle contract

* **Upgrade** — calendar migrations are versioned
  (`SUPPORTED_CALENDAR_VERSIONS = ("1.0", "2.0")`); an unsupported
  version fails safe and is never silently reinterpreted.  Unversioned
  stores are shape-validated at load; incompatible shapes fall back to
  the documented default.
* **Rollback** — calendar rollback restores the last validated backup
  (corrupt backups are rejected, the rollback is itself backed up).
  Everything else rolls back via `scripts/restore.sh`.
* **Backups** — `scripts/backup.sh` is bounded (30-day retention),
  timestamped, and restores only after interactive confirmation.
  Backups that include `.env` (secrets) must be stored securely.
* **Release manifest** — `data/state/release_manifest.json` records
  version, source revision, build id, dependency declarations, Python
  compatibility, and config/calendar schema versions.  It contains no
  secrets and is regenerated at build time
  (`scripts/build_release_manifest.py`).
