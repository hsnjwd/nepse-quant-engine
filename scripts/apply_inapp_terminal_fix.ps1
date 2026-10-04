# ============================================================
# apply_inapp_terminal_fix.ps1
#
# Auto-fix companion to fix_inapp_terminal.ps1 (diagnostic).
#
# The Freebuff in-app terminal invokes Git Bash through the
# standard Git-for-Windows layout:
#
#   C:\Program Files\Git\bin\bash.exe   (launcher shim)
#     -> ..\usr\bin\bash.exe            (real bash)
#
# When the real binary is missing (broken or non-standard Git
# install), this script:
#
#   1. auto-detects a working bash.exe (standard paths, git.exe
#      on PATH, bash.exe on PATH, Scoop, Chocolatey),
#   2. derives the Git root for a usr\bin\bash.exe layout,
#   3. creates the missing junction with elevation (UAC) so the
#      app's expected path resolves, then
#   4. verifies the result.
#
# It is idempotent: if the expected path already works it exits 0
# immediately. Run it directly, or via fix_inapp_terminal.bat.
#
# IMPORTANT: keep this file pure ASCII.  Non-ASCII characters are
# misread by Windows PowerShell 5.1 (no BOM) and break parsing.
# ============================================================

param(
    [string]$RealBash = "",
    [string]$RealRoot = ""
)

$ErrorActionPreference = "Stop"

$expectedRoot = "C:\Program Files\Git"
$expectedBash = "$expectedRoot\usr\bin\bash.exe"

function Test-PathP($path) {
    if (-not $path) { return $false }
    return Test-Path -LiteralPath $path
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- already fixed? -----------------------------------------
if (Test-PathP $expectedBash) {
    Write-Host "OK: $expectedBash already exists - nothing to fix."
    exit 0
}

# --- auto-detect a working bash.exe --------------------------
if (-not $RealBash) {
    $candidates = @()
    $candidates += "C:\Program Files\Git\usr\bin\bash.exe"
    $candidates += "$env:LOCALAPPDATA\Programs\Git\usr\bin\bash.exe"
    $candidates += "$env:USERPROFILE\scoop\apps\git\current\usr\bin\bash.exe"
    $candidates += "$env:ProgramData\chocolatey\lib\git\tools\usr\bin\bash.exe"

    $gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
    if ($gitExe) {
        $gitRoot = Split-Path -Path (Split-Path -Path $gitExe -Parent) -Parent
        $candidates += (Join-Path $gitRoot "usr\bin\bash.exe")
    }

    $bashOnPath = (Get-Command bash.exe -ErrorAction SilentlyContinue).Source
    if ($bashOnPath) {
        $candidates += $bashOnPath
    }

    $seen = @{}
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        $key = $c.ToLower()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        # Skip launcher shims (bin\bash.exe). They exist even in a broken
        # install and only re-exec ..\usr\bin\bash.exe, which is missing.
        if (-not $key.EndsWith("usr\bin\bash.exe")) { continue }
        if (Test-PathP $c) {
            $RealBash = $c
            Write-Host "Detected bash.exe: $RealBash"
            break
        }
    }
}

if (-not $RealBash) {
    Write-Host "ERROR: no bash.exe found on this machine."
    Write-Host "Install Git for Windows first, then re-run:"
    Write-Host "  winget install --id Git.Git -e --source winget"
    exit 1
}

# --- must be a Git-for-Windows usr\bin layout to junction ----
if (-not $RealRoot) {
    $parts = $RealBash.ToLower().Split("\")
    $isUsrBin = ($parts.Count -ge 3 -and $parts[$parts.Count - 2] -eq "bin" -and $parts[$parts.Count - 3] -eq "usr")
    if (-not $isUsrBin) {
        Write-Host "ERROR: bash.exe found at $RealBash, but it is not in a"
        Write-Host "Git-for-Windows usr\bin layout, so a junction cannot be"
        Write-Host "derived. Set the app's Terminal/Shell path to $RealBash,"
        Write-Host "or install Git for Windows at the standard location."
        exit 1
    }
    $RealRoot = Split-Path -Path (Split-Path -Path (Split-Path -Path $RealBash -Parent) -Parent) -Parent
    Write-Host "Derived Git root : $RealRoot"
}

# --- elevation ------------------------------------------------
if (-not (Test-Admin)) {
    Write-Host ""
    Write-Host "A junction under 'C:\Program Files' requires administrator"
    Write-Host "rights. A UAC prompt will appear - click Yes to continue."
    Write-Host ""
    $elevatedArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -RealBash `"$RealBash`" -RealRoot `"$RealRoot`""
    try {
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $elevatedArgs -PassThru -Wait | Out-Null
    } catch {
        Write-Host "ERROR: elevation was declined or failed: $($_.Exception.Message)"
        exit 1
    }
    if (Test-PathP $expectedBash) {
        Write-Host "OK: junction created and $expectedBash now resolves."
        exit 0
    }
    Write-Host "ERROR: the elevated fix did not complete. Run fix_inapp_terminal.bat"
    Write-Host "again, or create the junction manually (see the diagnostic output)."
    exit 1
}

# --- admin path: create the junction ---------------------------
Write-Host "Creating junction (elevated)..."
$target = Join-Path $RealRoot "usr"

if (Test-PathP $expectedRoot) {
    $usrPath = "$expectedRoot\usr"
    if (Test-PathP $usrPath) {
        $item = Get-Item -LiteralPath $usrPath -Force
        if ($item.LinkType -eq "Junction") {
            Write-Host "Existing junction '$usrPath' removed (will recreate)."
            cmd /c rmdir "$usrPath"
        } else {
            $backup = "$expectedRoot\usr.bak"
            if (Test-PathP $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
            Rename-Item -LiteralPath $usrPath -NewName "usr.bak"
            Write-Host "Existing folder moved to: $backup"
        }
    }
    New-Item -ItemType Junction -Path $usrPath -Target $target | Out-Null
    Write-Host "Junction created: $usrPath -> $target"
} else {
    # No Git install at the standard location at all: junction the root.
    New-Item -ItemType Junction -Path $expectedRoot -Target $RealRoot | Out-Null
    Write-Host "Junction created: $expectedRoot -> $RealRoot"
}

if (Test-PathP $expectedBash) {
    Write-Host "OK: $expectedBash now resolves. The in-app terminal should work"
    Write-Host "after you fully restart the Freebuff desktop app."
    exit 0
}

Write-Host "ERROR: junction created but $expectedBash still does not resolve."
exit 1
