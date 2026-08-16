# Release Checklist

Sprint 13.9 §23 — every item must be **verified**, not assumed.  A
release is not ready while any *critical* item is unknown.  Each item
states the exact command or check that closes it.

Related: [OPERATIONS.md](OPERATIONS.md), [STATE_INVENTORY.md](STATE_INVENTORY.md),
[DEPLOYMENT.md](DEPLOYMENT.md).

## Tests

- [ ] Full suite green: `python3 -m pytest -q` (0 failures; skipped
      tests must be documented).
- [ ] Sprint 13.9 suite green: `python3 -m pytest tests/test_sprint13_9.py -q`.
- [ ] Combined regression green:
      `python3 -m pytest tests/test_sprint13_2.py tests/test_sprint13_3.py
      tests/test_sprint13_4.py tests/test_sprint13_5.py tests/test_sprint13_6.py
      tests/test_sprint13_7.py tests/test_sprint13_8.py tests/test_sprint13_9.py
      tests/test_performance.py -q`.

## Performance

- [ ] Deterministic gate passes: `python3 -m benchmarks.ci_gate --factor 3.0
      --out benchmark-ci.json` (or the equivalent production gate).
- [ ] Thresholds unchanged and passing: warm API ratio ≤ 0.75,
      warm portfolio ratio ≤ 0.75, analyze p99 ≤ 2000 ms,
      cold/warm speedup ≥ 5.0 (Sprint 13.2 requirement).
- [ ] Release artifact shows no material regression vs the previous release.

## Security

- [ ] Artifact secret scan clean: `python3 scripts/verify_release_artifact.py dist/*.tar.gz`.
- [ ] No credentials committed: `git grep -nE "TELEGRAM_TOKEN\s*=|api[_-]?key\s*="` returns
      only placeholders (`.env.example` uses `your_*` values).
- [ ] No secrets in logs/metrics: check `/metrics` output for token/secret keys.
- [ ] Unsafe deserialization: JSON loads use `json_store` (no `pickle`/`yaml.load` in
      server paths).
- [ ] Filesystem writes reviewed: all persistent JSON writes route through
      `src/utils/json_store.py` (atomic + locking).
- [ ] Subprocess execution reviewed: launcher subprocesses use lists
      (no shell interpolation of untrusted input).
- [ ] Debug exposure reviewed: debug endpoints disabled in production
      (`ENABLE_PERFORMANCE_MONITORING` is observability, not debug).

## Configuration

- [ ] `python3 -c "from src.config.validation import validate_config; assert not validate_config()"`
      passes with the production `.env`.
- [ ] Invalid values fail clearly (import-time `ValueError` names the key;
      semantic ranges surfaced by `validate_config()`).
- [ ] Secrets externalized: `TELEGRAM_TOKEN` etc. come from environment /
      `.env`, never source.
- [ ] Production defaults are safe (`src/config/__init__.py` + `production.py`).

## State / database

- [ ] State inventory current: `src/utils/state_inventory.py` matches
      [STATE_INVENTORY.md](STATE_INVENTORY.md).
- [ ] All JSON state files valid: `python3 scripts/check_json_files.py`.
- [ ] Corruption matrix green (Sprint 13.9 test suite: valid / empty /
      truncated / malformed / wrong-schema / unexpected-version / partial-write /
      missing / permission-failure for every critical store).
- [ ] Atomic writes verified (temp-file + replace; fsync on calendar/manifest).
- [ ] Backup bounded: `sh scripts/backup.sh <tmpdir>` creates a timestamped archive;
      retention (30 days) documented and enforced.
- [ ] Restore validated: `sh scripts/restore.sh <backup.tar.gz>` restores after
      confirmation; validates before overwriting.

## Calendar

- [ ] Calendar loads with provenance: `/metrics` → `calendar_status` HEALTHY.
- [ ] Version supported: `SUPPORTED_CALENDAR_VERSIONS` covers the active file.
- [ ] Update + rollback cycle verified (Sprint 13.9 calendar tests).
- [ ] History bounded (CALENDAR_MAX_HISTORY).

## Providers / data

- [ ] Provider health observable: `/metrics` → `provider_health`.
- [ ] Fallback chain verified: API → CSV under provider failure.
- [ ] Data-quality counters bounded and observable.

## Docker

- [ ] `docker compose build` succeeds from a clean checkout.
- [ ] `docker compose up -d web api` starts; healthchecks green.
- [ ] Restart policy `unless-stopped` set for `web` and `api`.
- [ ] Persistent volumes declared for data/cache/logs.
- [ ] Two-worker `api` deployment verified (two pids observable in `/metrics`;
      worker failure recoverable).
- [ ] No secrets baked into the image (no `ENV TELEGRAM_TOKEN=...` literal).
- [ ] Only ports 8000/8501 exposed.

## API / scanner / alerts / monitoring

- [ ] `/health/live` and `/health/ready` behave per spec (ready 503 on
      broken critical initialization).
- [ ] Scanner deterministic (stable ranking) on the shipped corpus.
- [ ] Alert pipeline safe: no unsafe signal path; stateful alerts bounded.
- [ ] `/metrics` bounded and secret-free.

## Rollback

- [ ] Release N → N+1 → detect failure → rollback to N exercised on a
      staging clone; state remains readable; calendar valid; no corrupted
      migration remains.

## Release artifact / git status

- [ ] `git status` clean at the tagged commit.
- [ ] VERSION matches the changelog release entry (currently VERSION =
      `1.0.0-rc1` while the changelog is at `v1.0.0-rc7` — **bump VERSION
      before tagging**).
- [ ] Manifest generated and verified:
      `python3 scripts/build_release_manifest.py --verify`.
- [ ] Artifact built and verified:
      `sh scripts/build_release_artifact.sh dist/`.
- [ ] CI release-validation workflow green on the tag.

---

## Uncommitted-work audit (Sprint 13.9 §4)

Run before every release commit.  Do **not** blindly commit everything;
classify each item and verify required production source/tests/docs never
depend on an untracked file.

```bash
git status --short
git diff --stat
git ls-files
```

Classes: `REQUIRED SOURCE`, `REQUIRED TEST`, `REQUIRED DOCUMENTATION`
(must be committed with the release), `GENERATED ARTIFACT`,
`LOCAL STATE`, `TEMPORARY FILE`, `OBSOLETE` (never commit).

Sprint 13.9 work tree (point-in-time audit, before commit):

| Path | Class | Notes |
|---|---|---|
| `src/utils/release_manifest.py`, `release_artifact.py`, `state_inventory.py`, `src/config/validation.py` | REQUIRED SOURCE | new modules; referenced by scripts/tests |
| `scripts/build_release_artifact.sh`, `build_release_manifest.py`, `build_source_tarball.py`, `verify_release_artifact.py` | REQUIRED SOURCE | release pipeline |
| `tests/test_sprint13_9.py` | REQUIRED TEST | referenced by `release.yml` |
| `docs/OPERATIONS.md`, `docs/RELEASE_CHECKLIST.md`, `docs/STATE_INVENTORY.md` | REQUIRED DOCUMENTATION | required by `verify_release_artifact` |
| Modified tracked files (Dockerfile, compose, workflows, `src/api/health.py`, `src/config/__init__.py`, `src/data/calendar.py`, `src/utils/json_store.py`, benchmarks, launcher, changelog, 13.x test files) | REQUIRED SOURCE / TEST / DOC | Sprint 13.9 changes |
| `data/state/release_manifest.json`, `worker_metrics.json`, `nepse_calendar_history.json` | GENERATED ARTIFACT | gitignored; regenerated |
| `data/raw/syn*.csv`, other gitignored `data/raw/*.csv` | GENERATED ARTIFACT / LOCAL STATE | benchmark corpus + local data; gitignored |
| `.pytest_tmp/`, `logs/`, `pids/`, `benchmarks/results/`, `dist/`, `.freebuff/` | TEMPORARY / LOCAL STATE | never commit; excluded from the release artifact |

Release-artifact consequence: `src/utils/release_artifact.py` excludes all
GENERATED ARTIFACT / LOCAL STATE / TEMPORARY classes above, so an
artifact built from a dirty tree carries the same content as one built
from a clean checkout.

---

## Release classification

| Class | Meaning |
|---|---|
| READY | all critical items verified |
| CONDITIONALLY_READY | non-critical items unknown, each with documented risk/mitigation/operator action |
| NOT_READY | any critical item fails or is unknown |

A release is NOT_READY if tests fail, the performance gate fails, the
artifact scan finds secrets, or the Docker deployment does not start.
