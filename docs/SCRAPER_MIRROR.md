# Scraper Mirror — Own the NEPSE Data Pipeline

**Goal:** make the NEPSE-All-Scraper data pipeline fully ours — a **private** GitHub
Actions mirror that runs the daily scrape on *our* account, independent of the
third-party repo (`SamirWagle/Nepse-All-Scraper`).

## Why a private non-fork mirror?

Two facts drive the design:

1. **A fork already exists** — `hsnjwd/Nepse-All-Scraper` is a fork of the
   upstream repo, and the local clone at `~/Desktop/Nepse-All-Scraper` already
   points at it (`origin`, branch `main`, in sync at `a8d8380`).
2. **Forks don't run scheduled workflows.** GitHub's docs are explicit:
   *"Workflows don't run in forked repositories by default. You must enable
   GitHub Actions in the Actions tab of the forked repository."* — and the
   `schedule` (cron) event is not supported in forks at all.

So the public fork is useless for automation. The correct architecture is a
**brand-new private repository that is NOT a fork**, seeded from the clone's
full history. In a non-fork repo, GitHub Actions runs on schedule normally:

| Repo | Daily cron runs? | Private? | We own data? |
| --- | --- | --- | --- |
| Public fork `hsnjwd/Nepse-All-Scraper` | ❌ (fork) | ❌ | ❌ |
| **Private mirror `nepse-scraper-mirror`** (this plan) | ✅ | ✅ | ✅ |

## What the mirror gives you

- `daily_scraper.yml` (already in the repo) runs **weekdays 12:45 UTC**, scrapes
  prices / dividends / right-shares / floorsheet / indices, and commits the data
  **back to our private repo**.
- `sync_upstream.yml` (new) runs **weekdays 13:30 UTC** and pulls *scraper code*
  fixes from upstream — so we keep their improvements without depending on them
  staying online. It deliberately **never touches `data/` or `docs/`** (we
  generate our own) and syncs `.github/` per-file so mirror-only workflows
  survive upstream updates.
- `health_check.yml` (new) runs after every **Daily Scraper** and verifies the
  scrape actually landed — data freshness, the NABIL price series, and the
  `docs/api` build — filing an issue on the mirror if anything is broken.
- Full history + data are preserved (the push carries everything).
- Upstream can disappear tomorrow and the pipeline keeps running.

## One-shot setup (recommended)

From the project root, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\scraper_mirror\setup_private_mirror.ps1
```

**Authentication — the script works three ways, pick one:**

1. **GitHub CLI** (easiest): `winget install GitHub.cli`, then `gh auth login`,
   then run the script.
2. **Personal access token** (no installs): create a classic token with the
   `repo` scope at <https://github.com/settings/tokens>, then in the *same*
   PowerShell window:
   ```powershell
   $env:GITHUB_TOKEN = "ghp_YOUR_TOKEN"
   powershell -ExecutionPolicy Bypass -File .\scripts\scraper_mirror\setup_private_mirror.ps1
   ```
3. **Manual + skip create**: create the empty **private** repo in the browser
   at <https://github.com/new> (name it `nepse-scraper-mirror`), then run the
   script with `-SkipCreate`; git will prompt for credentials on the push.

Customize if you have a real GitHub org or want a different name:

```powershell
.\scripts\scraper_mirror\setup_private_mirror.ps1 `
  -Owner MyOrg `
  -MirrorName nepse-data-pipeline `
  -LocalClone C:\Users\User\Desktop\Nepse-All-Scraper
```

The script:
1. Preflight-checks git, the local clone, and that some auth is available.
2. Creates the **private** repo (or reuses it if it already exists).
3. Pushes `main` + tags from the local clone (full history + data).
4. Installs the mirror-only workflows (`sync_upstream.yml` + `health_check.yml`)
   into `.github/workflows/` and pushes them.
5. Verifies visibility and prints next steps.

## Manual equivalent (no script, no gh)

```powershell
cd C:\Users\User\Desktop\Nepse-All-Scraper

# Create the private repo in the browser first: https://github.com/new
# (name: nepse-scraper-mirror, Private), then push. Git will prompt for
# credentials, or set $env:GITHUB_TOKEN first and use the tokenized URL.
git remote add mirror https://github.com/hsnjwd/nepse-scraper-mirror.git

# Push full history (data + workflows) and tags
git push mirror main
git push mirror --tags

# Install the mirror-only workflows (absolute source — the templates live in
# the Quant Engine repo, while these commands run in the scraper clone)
Copy-Item C:\Users\User\Documents\nepse-quant-engine\scripts\scraper_mirror\sync_upstream.yml `
          .\.github\workflows\sync_upstream.yml
Copy-Item C:\Users\User\Documents\nepse-quant-engine\scripts\scraper_mirror\health_check.yml `
          .\.github\workflows\health_check.yml
git add .github/workflows/sync_upstream.yml .github/workflows/health_check.yml
git commit -m "chore: add mirror workflows (upstream sync + health check)"
git pull --rebase mirror main   # safe if the daily scrape pushed since setup
git push mirror main
```

## Verify it works (2 minutes)

1. Open `https://github.com/hsnjwd/nepse-scraper-mirror/actions`.
   Actions should be enabled automatically (non-fork repos).
2. Kick off the first scrape: **Actions → Daily Scraper → Run workflow**.
   It scrapes, commits `data/`, and pushes back to the mirror.
3. Check the badge / latest run at
   `https://github.com/hsnjwd/nepse-scraper-mirror/actions/workflows/daily_scraper.yml`.
4. (Optional) Archive or delete the old public fork `hsnjwd/Nepse-All-Scraper`
   once the mirror's first run succeeds.

## Scrape health check

`health_check.yml` runs automatically after every **Daily Scraper** run and
verifies the scrape actually landed:

| Check | Verifies |
| --- | --- |
| Data freshness | a commit has touched `data/` within the window (default 5 days) and a floorsheet CSV exists |
| NABIL price series | `data/company-wise/NABIL/prices.csv` exists with ≥ N rows (default 250) |
| `docs/api` build | `build_api.py` output is present (`companies.json`, `latest.json`, `status.json`, `openapi.json`, `index.html`, `.nojekyll`) and lists ≥ 200 symbols |

If a check fails it files (or updates) an issue titled
`[Scrape Health] FAILED on <date>` and marks the run red — no issue filed means
healthy. If the Daily Scraper itself fails, it files a "Daily Scraper did not
complete" issue instead (no point verifying data that was never scraped).

Run it manually: **Actions → Scrape Health Check → Run workflow**; the
`min_nabil_rows` / `freshness_days` inputs tighten or loosen the thresholds.

> If the mirror was created before this workflow existed, install it with the
> manual commands above, or simply re-run `setup_private_mirror.ps1` — it now
> installs both mirror workflows and is safe to re-run (it rebases onto the
> mirror first, so it won't fail if the daily scrape already pushed).

## Consuming the mirror from the Quant Engine

Once the mirror is live, point the engine's data layer at it instead of the
third-party GitHub Pages API:

- Add a provider URL in `src/config.py` →
  `DATA_SERVICE_API_URLS["github_datasets"] =
  "https://raw.githubusercontent.com/hsnjwd/nepse-scraper-mirror/main/docs/api"`
  (`build_api.py` writes the JSON to `docs/api/`, so the **raw** path must
  include `docs/`; plain `/api` is the GitHub **Pages** URL, which on a private
  repo requires a paid plan) — or better, use a fine-grained **read-only PAT**
  if the repo stays private.
- For the standalone UI: fetch
  `https://api.github.com/repos/hsnjwd/nepse-scraper-mirror/contents/docs/api/...`
  with a token, or enable GitHub Pages on the mirror (private Pages works with a
  paid plan; otherwise read via `raw.githubusercontent.com` + token).

## Notes & limitations

- **Private repo + scheduled Actions** consumes GitHub Actions minutes
  (free tier: 2,000 min/month for private repos). The daily scrape is well
  within that.
- `sync_upstream.yml` pulls code paths only (scraper code, requirements,
  README, `.gitignore`, and the `daily_scraper.yml` workflow). It never touches
  `data/` or `docs/` (both are regenerated by our own scrape/build) and syncs
  `.github/` per-file so mirror-only workflows (`sync_upstream.yml`,
  `health_check.yml`) survive upstream updates. If upstream *renames/deletes* a
  code file, a manual conflict resolution may be needed once — the workflow
  logs an error if that happens.
- The scraper is licensed "educational" — fine for this research/educational
  project; keep data redistribution non-commercial.
