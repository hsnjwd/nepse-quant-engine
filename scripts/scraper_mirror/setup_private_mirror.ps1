#Requires -Version 5.1
<#
.SYNOPSIS
    Create a PRIVATE GitHub Actions mirror of Nepse-All-Scraper that we fully own.

.DESCRIPTION
    The public fork hsnjwd/Nepse-All-Scraper already exists, but GitHub does
    NOT run `schedule` (cron) workflows in forked repositories. This script
    creates a brand-new PRIVATE, non-fork repository seeded from the local
    clone's full history, so:

      * The existing .github/workflows/daily_scraper.yml runs on schedule
        (weekdays 12:45 UTC) and commits scraped data back to OUR repo.
      * A new sync_upstream.yml keeps the scraper CODE synced from the
        third-party repo (SamirWagle/Nepse-All-Scraper) so we keep their
        fixes without depending on them staying online.
      * A health_check.yml workflow verifies every daily scrape (data
        freshness, NABIL price series, docs/api build) and files an issue
        if anything looks broken.
      * The pipeline is owned by us; upstream becomes optional.

    AUTHENTICATION (choose ONE):
      1. GitHub CLI:  install gh (https://cli.github.com/) then run:  gh auth login
      2. PAT:         create a classic token (scope: repo) at
                      https://github.com/settings/tokens  then in THIS window run:
                          $env:GITHUB_TOKEN = "ghp_YOUR_TOKEN"
                      and re-run the script.
      3. Manual:      create the empty PRIVATE repo in the browser at
                      https://github.com/new  then re-run with  -SkipCreate
                      (git will prompt for credentials, or combine with option 2).

    IMPORTANT: this file must stay pure ASCII (7-bit). Windows PowerShell 5.1
    reads .ps1 files without a BOM as ANSI; non-ASCII bytes such as em-dashes
    are misread as Windows-1252 (byte 0x94 becomes a double-quote) and break
    parsing. Do not reintroduce non-ASCII characters.

.PARAMETER Owner
    GitHub account or org that will own the mirror. Default: hsnjwd

.PARAMETER MirrorName
    Name of the new private repository. Default: nepse-scraper-mirror

.PARAMETER LocalClone
    Path to the local Nepse-All-Scraper clone. Default: Desktop clone.

.PARAMETER UpstreamUrl
    Third-party upstream repo to sync code from. Default: SamirWagle.

.PARAMETER SkipCreate
    Skip repo creation (use when the repo was already created in the browser).

.EXAMPLE
    .\scripts\scraper_mirror\setup_private_mirror.ps1
    .\scripts\scraper_mirror\setup_private_mirror.ps1 -Owner MyOrg -MirrorName nepse-data-pipeline
#>
[CmdletBinding()]
param(
    [string]$Owner = "hsnjwd",
    [string]$MirrorName = "nepse-scraper-mirror",
    [string]$LocalClone = "C:\Users\User\Desktop\Nepse-All-Scraper",
    [string]$UpstreamUrl = "https://github.com/SamirWagle/Nepse-All-Scraper.git",
    [switch]$SkipCreate
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Assert-ExitZero([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "Failed: $what" }
}

$apiBase  = "https://api.github.com"
$repoPath = "$Owner/$MirrorName"
$repoUrl  = "https://github.com/$repoPath"
$auth     = $env:GITHUB_TOKEN
$hasGh    = [bool](Get-Command gh -ErrorAction SilentlyContinue)

# ---- 0. Preflight --------------------------------------------------------
Write-Step "Preflight checks"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git not found. Install Git for Windows: https://git-scm.com/download/win"
}
if (-not (Test-Path $LocalClone)) {
    throw "Local clone not found: $LocalClone"
}
if (-not $SkipCreate -and -not $hasGh -and -not $auth) {
    Write-Host "No GitHub CLI (gh) and no GITHUB_TOKEN detected." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Choose one:" -ForegroundColor Yellow
    Write-Host "  A) Install gh:  winget install GitHub.cli   then:  gh auth login"
    Write-Host "  B) Use a PAT:   create a classic token (scope: repo) at"
    Write-Host "     https://github.com/settings/tokens  then in THIS window run:"
    Write-Host "         `$env:GITHUB_TOKEN = `"ghp_YOUR_TOKEN`""
    Write-Host "     and re-run this script."
    Write-Host "  C) Create the private repo manually at https://github.com/new"
    Write-Host "     then re-run with:  -SkipCreate"
    throw "No GitHub authentication available. See the options above."
}

if ($hasGh) {
    gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "gh is not authenticated. Run: gh auth login"
    }
    Write-Host "Using GitHub CLI as: $((gh api user --jq .login))"
} elseif ($auth) {
    Write-Host "Using GITHUB_TOKEN (personal access token)."
} else {
    Write-Host "No gh, no token: will push using git's credential prompt (-SkipCreate mode)."
}

# ---- 1. Create the private repo (if needed) ------------------------------
if (-not $SkipCreate) {
    Write-Step "Creating private repo: $repoUrl"
    if ($hasGh) {
        gh repo view $repoPath *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Repo already exists - reusing it."
        } else {
            gh repo create $repoPath --private
            Assert-ExitZero "creating $repoUrl"
            Write-Host "Created $repoUrl"
        }
    } else {
        # Check existence first via API (avoids a noisy 422 on create).
        try {
            Invoke-RestMethod -Method Get -Uri "$apiBase/repos/$repoPath" `
                -Headers @{ Authorization = "Bearer $auth" } | Out-Null
            Write-Host "Repo already exists - reusing it."
        } catch {
            $body = @{ name = $MirrorName; private = $true } | ConvertTo-Json
            try {
                Invoke-RestMethod -Method Post -Uri "$apiBase/user/repos" `
                    -Headers @{ Authorization = "Bearer $auth" } `
                    -Body $body -ContentType "application/json" | Out-Null
                Write-Host "Created $repoUrl (personal account)"
            } catch {
                # Owner may be an organization, not a personal account.
                Invoke-RestMethod -Method Post -Uri "$apiBase/orgs/$Owner/repos" `
                    -Headers @{ Authorization = "Bearer $auth" } `
                    -Body $body -ContentType "application/json" | Out-Null
                Write-Host "Created $repoUrl (org account)"
            }
        }
    }
} else {
    Write-Step "Skipping repo creation (-SkipCreate) - expecting $repoUrl to already exist"
}

# ---- 2. Push full history from the local clone ---------------------------
Write-Step "Pushing full history (includes daily_scraper.yml + data/)"

Push-Location $LocalClone
try {
    git remote remove mirror 2>$null
    git remote add mirror $repoUrl

    # Re-run safety: on a SECOND run the mirror may already contain commits we
    # do not have locally (installed workflow commits, daily scrape data).
    # Fetch + rebase first so the push below is a fast-forward, not a rejected
    # non-fast-forward. First run: the new mirror has no main yet - skip.
    if ($hasGh) {
        gh auth setup-git | Out-Null
        $fetchUrl = $repoUrl
    } elseif ($auth) {
        $fetchUrl = "https://x-access-token:$auth@github.com/$repoPath"
    } else {
        $fetchUrl = $repoUrl
    }
    git fetch $fetchUrl main 2>$null
    if ($LASTEXITCODE -eq 0) {
        git rebase FETCH_HEAD
        Assert-ExitZero "rebasing local clone onto mirror/main"
    } else {
        Write-Host "Mirror main is empty (first run) - nothing to rebase onto."
    }

    if ($hasGh) {
        git push mirror main
        Assert-ExitZero "pushing main to $repoUrl"
        git push mirror --tags 2>$null
        Write-Host "Pushed main (and tags) -> $repoUrl"
    } elseif ($auth) {
        # One-shot push with the token in the URL; never written into .git/config.
        $authUrl = "https://x-access-token:$auth@github.com/$repoPath"
        git push $authUrl main
        Assert-ExitZero "pushing main to $repoUrl"
        git push $authUrl --tags 2>$null
        Write-Host "Pushed main (and tags) -> $repoUrl (clean remote 'mirror' stored)"
    } else {
        # No token: push to the plain URL and let git / Git Credential Manager prompt.
        git push $repoUrl main
        Assert-ExitZero "pushing main to $repoUrl"
        git push $repoUrl --tags 2>$null
        Write-Host "Pushed main (and tags) -> $repoUrl"
    }
} finally {
    Pop-Location
}

# ---- 3. Install the mirror-only workflows --------------------------------
Write-Step "Installing mirror workflows (sync_upstream.yml + health_check.yml)"
$workflowFiles = @("sync_upstream.yml", "health_check.yml")
foreach ($wf in $workflowFiles) {
    $wfSrc = Join-Path $PSScriptRoot $wf
    if (-not (Test-Path $wfSrc)) {
        throw "Missing workflow template: $wfSrc (must live next to this script)"
    }
    $wfDst = Join-Path $LocalClone ".github\workflows\$wf"
    New-Item -ItemType Directory -Force -Path (Split-Path $wfDst) | Out-Null
    Copy-Item -Force $wfSrc $wfDst
}

Push-Location $LocalClone
try {
    git add .github/workflows/sync_upstream.yml .github/workflows/health_check.yml
    # NOTE: do NOT write `if (-not (git diff --cached --quiet))` - git --quiet
    # prints nothing, so -not $null is always $true. Check the exit code instead.
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m "chore: add mirror workflows (upstream sync + health check)"
        if ($hasGh) {
            git push mirror main
            Assert-ExitZero "pushing mirror workflows"
        } elseif ($auth) {
            $authUrl = "https://x-access-token:$auth@github.com/$repoPath"
            git push $authUrl main
            Assert-ExitZero "pushing mirror workflows"
        } else {
            git push mirror main
            Assert-ExitZero "pushing mirror workflows"
        }
    } else {
        Write-Host "Mirror workflows already up to date in mirror."
    }
} finally {
    Pop-Location
}

# ---- 4. Verify + report ---------------------------------------------------
Write-Step "Verifying"
if ($hasGh) {
    gh repo view $repoPath --json name,visibility,isPrivate,url |
        ForEach-Object { Write-Host $_ }
} elseif ($auth) {
    try {
        $r = Invoke-RestMethod -Method Get -Uri "$apiBase/repos/$repoPath" `
            -Headers @{ Authorization = "Bearer $auth" }
        Write-Host ("name={0} visibility={1} private={2} url={3}" -f `
            $r.name, $r.visibility, $r.private, $r.html_url)
    } catch {
        Write-Host "Could not verify via API - check the repo page: $repoUrl"
    }
} else {
    Write-Host "Open the repo page to verify: $repoUrl"
}

Write-Host ""
Write-Host "SUCCESS. Private mirror is live: $repoUrl" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps (2 min):"
Write-Host "  1. Open  $repoUrl/actions  -> Actions should be enabled (non-fork repos run by default)."
Write-Host "  2. Run the daily scrape now: Actions -> 'Daily Scraper' -> 'Run workflow' (workflow_dispatch)."
Write-Host "  3. Verify the scheduled cron shows in the workflow YAML (weekdays 12:45 UTC)."
Write-Host "  4. Scrape Health Check runs after each Daily Scraper and files an"
Write-Host "     issue if data freshness / NABIL / docs-api look broken."
Write-Host "  5. Optional: delete or archive the old public fork hsnjwd/Nepse-All-Scraper."
Write-Host ""
Write-Host "To consume this mirror from the Quant Engine later, see docs/SCRAPER_MIRROR.md"
