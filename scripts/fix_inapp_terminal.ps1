# ============================================================
# fix_inapp_terminal.ps1
#
# The Freebuff in-app terminal fails with:
#
#   Skipping command-line '"C:\Program Files\Git\bin\..\usr\bin\bash.exe"'
#   ('C:\Program Files\Git\bin\..\usr\bin\bash.exe' not found)
#   Need a valid command-line; Edit the string resources accordingly
#
# Cause: the app invokes Git Bash through the standard Git for
# Windows layout.  C:\Program Files\Git\bin\bash.exe is a launcher
# shim that execs ..\usr\bin\bash.exe.  When that real binary is
# missing (broken/partial Git install, or Git installed elsewhere),
# the shim prints the error above and every command fails.
#
# This script locates the real bash.exe on your machine, checks the
# app's expected path, and prints the exact fix to run.  Run it from
# your own PowerShell (no admin needed to diagnose):
#
#   powershell -ExecutionPolicy Bypass -File scripts\fix_inapp_terminal.ps1
#
# IMPORTANT: keep this file pure ASCII.  Non-ASCII characters are
# misread by Windows PowerShell 5.1 (no BOM) and break parsing.
# ============================================================

$ErrorActionPreference = "Continue"

function Test-PathP($path) {
    if (-not $path) { return $false }
    return Test-Path -LiteralPath $path
}

Write-Host ""
Write-Host "=== In-app terminal: Git Bash diagnostic ==="
Write-Host ""

$expectedShim = "C:\Program Files\Git\bin\bash.exe"
$expectedBash = "C:\Program Files\Git\usr\bin\bash.exe"

Write-Host "App expects (shim): $expectedShim"
Write-Host "App expects (real): $expectedBash"
Write-Host ""

# --- Where is the app's own config? (diagnostic only) ---------
$appCfgDirs = @(
    "$env:APPDATA\@codebufffreebuff-desktop",
    "$env:LOCALAPPDATA\@codebufffreebuff-desktop"
)
foreach ($dir in $appCfgDirs) {
    if (Test-PathP $dir) {
        Write-Host "App config dir found: $dir"
        $hits = Get-ChildItem -Path $dir -Recurse -Include *.json,*.config,*.cfg -ErrorAction SilentlyContinue |
            Select-String -Pattern "bash" -SimpleMatch -ErrorAction SilentlyContinue |
            Select-Object -First 10
        foreach ($h in $hits) {
            if ($h.Line) {
                Write-Host ("  [config ref] " + $h.Path + " -> " + $h.Line.Trim())
            }
        }
    }
}
Write-Host ""

# --- Collect candidate bash locations --------------------------
$candidates = @()
$candidates += "C:\Program Files\Git\usr\bin\bash.exe"
$candidates += "$env:LOCALAPPDATA\Programs\Git\usr\bin\bash.exe"
$candidates += "$env:USERPROFILE\scoop\apps\git\current\usr\bin\bash.exe"
$candidates += "$env:ProgramData\chocolatey\lib\git\tools\usr\bin\bash.exe"

# Derive a candidate from git.exe on PATH, if any.
$gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if ($gitExe) {
    Write-Host "git on PATH : $gitExe"
    $gitRoot = Split-Path -Path (Split-Path -Path $gitExe -Parent) -Parent
    $derived = Join-Path $gitRoot "usr\bin\bash.exe"
    Write-Host "derived bash: $derived"
    $candidates += $derived
} else {
    Write-Host "git.exe     : not found on PATH"
}

$bashOnPath = (Get-Command bash.exe -ErrorAction SilentlyContinue).Source
if ($bashOnPath) {
    Write-Host "bash on PATH: $bashOnPath"
    $candidates += $bashOnPath
} else {
    Write-Host "bash.exe    : not found on PATH"
}

# --- Find the first real bash -----------------------------------
$realBash = $null
$seen = @{}
foreach ($c in $candidates) {
    if (-not $c) { continue }
    $key = $c.ToLower()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    if (Test-PathP $c) {
        Write-Host "FOUND bash  : $c"
        if (-not $realBash) { $realBash = $c }
    }
}
Write-Host ""

$shimExists = Test-PathP $expectedShim
$realExists = Test-PathP $expectedBash

Write-Host "=== Diagnosis ==="
if ($realExists) {
    Write-Host "OK: $expectedBash exists."
    Write-Host "The in-app terminal should work. If it still fails,"
    Write-Host "fully quit and restart the Freebuff desktop app."
    exit 0
}

if ($shimExists) {
    Write-Host "PARTIAL INSTALL: $expectedShim exists but $expectedBash is missing."
    Write-Host "Your Git for Windows install at C:\Program Files\Git is broken"
    Write-Host "(e.g. interrupted update or antivirus quarantine)."
} elseif ($realBash) {
    Write-Host "MISMATCH: Git Bash exists at $realBash"
    Write-Host "but not at the app's expected standard path."
} else {
    Write-Host "Git Bash is missing entirely on this machine."
}
Write-Host ""

Write-Host "=== Fix options (run one) ==="
Write-Host ""

Write-Host "OPTION A - Repair Git for Windows at the standard path (recommended)."
Write-Host "  winget install --id Git.Git -e --source winget"
Write-Host "  or re-run the installer from https://git-scm.com/download/win"
Write-Host ""

if ($realBash) {
    $parts = $realBash.ToLower().Split("\")
    $isUsrBin = ($parts.Count -ge 3 -and $parts[$parts.Count - 2] -eq "bin" -and $parts[$parts.Count - 3] -eq "usr")
    if ($isUsrBin) {
        $realRoot = Split-Path -Path (Split-Path -Path (Split-Path -Path $realBash -Parent) -Parent) -Parent
        Write-Host "OPTION B - Point the standard path at your existing Git via a junction."
        Write-Host "Real Git root: $realRoot"
        Write-Host "Run PowerShell AS ADMINISTRATOR, then:"
        Write-Host ""
        if (-not (Test-PathP "C:\Program Files\Git")) {
            Write-Host "  New-Item -ItemType Junction -Path 'C:\Program Files\Git' -Target '$realRoot'"
        } else {
            Write-Host "  New-Item -ItemType Junction -Path 'C:\Program Files\Git\usr' -Target '$realRoot\usr'"
        }
        Write-Host ""
        Write-Host "Verify afterwards (should print True):"
        Write-Host "  Test-Path 'C:\Program Files\Git\usr\bin\bash.exe'"
        Write-Host ""
        Write-Host "Note: New-Item -ItemType Junction fails if the target path already"
        Write-Host "exists. If the command errors, remove the leftover folder first"
        Write-Host "(e.g. Remove-Item 'C:\Program Files\Git\usr' -Force) then re-run it."
        Write-Host ""
    }
}

Write-Host "OPTION C - If the Freebuff app Settings expose a Terminal / Shell"
Write-Host "path option, point it directly at:"
if ($realBash) {
    Write-Host "  $realBash"
} else {
    Write-Host "  <path to a working bash.exe after installing Git for Windows>"
}
Write-Host ""

Write-Host "After applying a fix, fully restart the Freebuff desktop app"
Write-Host "and the in-app terminal should start working."
Write-Host ""
