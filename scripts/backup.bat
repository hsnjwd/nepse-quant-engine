@echo off
REM =====================================================================
REM NEPSE Quant Engine — Backup Script (Windows)
REM =====================================================================
REM Usage:
REM   scripts\backup.bat                Default backup to .\backups\
REM   scripts\backup.bat D:\backups     Custom backup directory
REM =====================================================================
setlocal enabledelayedexpansion

title NEPSE Quant Engine Backup

echo ========================================================
echo   NEPSE QUANT ENGINE — BACKUP
echo ========================================================

REM ── Project root (parent of scripts folder) ────────────────────
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."

REM ── Configuration ──────────────────────────────────────────────
set "BACKUP_DIR=%~1"
if "%BACKUP_DIR%"=="" set "BACKUP_DIR=%PROJECT_DIR%\backups"

REM Timestamp-safe folder name
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "DT=%%I"
if "%DT%"=="" set "DT=%DATE:~-4%%DATE:~4,2%%DATE:~7,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "DT=%DT: =0%"
set "BACKUP_NAME=nepse_quant_engine_backup_%DT%"
set "BACKUP_PATH=%BACKUP_DIR%\%BACKUP_NAME%"

REM ── Prerequisites ──────────────────────────────────────────────
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
if not exist "%BACKUP_PATH%" mkdir "%BACKUP_PATH%"

echo   Source:      %PROJECT_DIR%
echo   Destination: %BACKUP_PATH%
echo ========================================================

REM ── 1. Market data (CSV files) ──────────────────────────────────
if exist "%PROJECT_DIR%\data" (
    echo [1/4] Backing up market data...
    xcopy /E /I /Q "%PROJECT_DIR%\data" "%BACKUP_PATH%\data" >nul
    echo       CSV files copied
) else (
    echo [1/4] No data directory found -- skipping
)

REM ── 2. Environment configuration ──────────────────────────────────
if exist "%PROJECT_DIR%\.env" (
    echo [2/4] Backing up .env configuration...
    copy "%PROJECT_DIR%\.env" "%BACKUP_PATH%\.env" >nul
    echo       .env backed up
) else (
    echo [2/4] No .env file found -- skipping
)

REM ── 3. User cache and settings ─────────────────────────────────────
if exist "%USERPROFILE%\.nepse" (
    echo [3/4] Backing up cache and settings...
    xcopy /E /I /Q "%USERPROFILE%\.nepse" "%BACKUP_PATH%\nepse_home" >nul
    echo       Cache backed up
) else (
    echo [3/4] No .nepse found -- skipping
)

REM ── 4. Log files ───────────────────────────────────────────────────
if exist "%PROJECT_DIR%\logs" (
    echo [4/4] Backing up logs...
    xcopy /E /I /Q "%PROJECT_DIR%\logs" "%BACKUP_PATH%\logs" >nul
    echo       Logs backed up
) else (
    echo [4/4] No logs directory found -- skipping
)

REM ── Create archive ─────────────────────────────────────────────────
echo.
echo Creating archive...

REM Use PowerShell if available for .zip creation
powershell -Command "Compress-Archive -Path '%BACKUP_PATH%' -DestinationPath '%BACKUP_PATH%.zip' -Force" >nul 2>&1

if exist "%BACKUP_PATH%.zip" (
    rmdir /S /Q "%BACKUP_PATH%"
    echo   Created: %BACKUP_PATH%.zip
    for %%A in ("%BACKUP_PATH%.zip") do echo   Size: %%~zA bytes
) else (
    echo   WARNING: Archive creation failed. Uncompressed backup at:
    echo   %BACKUP_PATH%
)

echo ========================================================
echo   Backup complete!
echo ========================================================
pause
