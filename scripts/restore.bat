@echo off
REM =====================================================================
REM NEPSE Quant Engine — Restore Script (Windows)
REM =====================================================================
REM Usage:
REM   scripts\restore.bat                              Latest backup
REM   scripts\restore.bat backups\nepse_..._20240730.zip   Specific file
REM =====================================================================
setlocal enabledelayedexpansion

title NEPSE Quant Engine Restore

echo ========================================================
echo   NEPSE QUANT ENGINE — RESTORE
echo ========================================================

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."

REM ── Locate backup archive ──────────────────────────────────────
set "BACKUP_FILE=%~1"
if "%BACKUP_FILE%"=="" (
    REM Find the most recent .zip backup
    set "BACKUP_FILE="
    for /f "tokens=*" %%F in ('dir "%PROJECT_DIR%\backups\nepse_quant_engine_backup_*.zip" /b /o-d 2^>nul') do (
        if not defined BACKUP_FILE set "BACKUP_FILE=%%F"
    )
    if not defined BACKUP_FILE (
        echo ERROR: No backup found in %PROJECT_DIR%\backups\
        pause
        exit /b 1
    )
    set "BACKUP_FILE=%PROJECT_DIR%\backups\!BACKUP_FILE!"
    echo   Auto-detected: !BACKUP_FILE!
) else (
    if not exist "%BACKUP_FILE%" (
        echo ERROR: File not found: %BACKUP_FILE%
        pause
        exit /b 1
    )
)

echo   Source: %BACKUP_FILE%

REM ── Confirm ─────────────────────────────────────────────────────
echo.
echo   WARNING: This will OVERWRITE current data with backup data.
set /p CONFIRM="  Continue? [y/N] "
if /I not "%CONFIRM%"=="y" (
    echo   Restore cancelled.
    pause
    exit /b 0
)

REM ── Extract to a temp directory ─────────────────────────────────
set "TEMP_DIR=%TEMP%\nepse_restore_%RANDOM%"
mkdir "%TEMP_DIR%"

echo [1/4] Extracting backup...
powershell -Command "Expand-Archive -Path '%BACKUP_FILE%' -DestinationPath '%TEMP_DIR%'" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   ERROR: Failed to extract archive. Is PowerShell available?
    rmdir /S /Q "%TEMP_DIR%" 2>nul
    pause
    exit /b 1
)

REM Find the extracted folder
for /d %%D in ("%TEMP_DIR%\*") do set "EXTRACTED=%%D"
if not defined EXTRACTED set "EXTRACTED=%TEMP_DIR%"

echo   Extracted to: %EXTRACTED%

REM ── Restore data ─────────────────────────────────────────────────
if exist "%EXTRACTED%\data" (
    echo [2/4] Restoring market data...
    if exist "%PROJECT_DIR%\data\raw" rmdir /S /Q "%PROJECT_DIR%\data\raw"
    xcopy /E /I /Q "%EXTRACTED%\data" "%PROJECT_DIR%\data" >nul
    echo       Data restored
)

REM ── Restore .env ─────────────────────────────────────────────────
if exist "%EXTRACTED%\.env" (
    echo [3/4] Restoring .env configuration...
    copy /Y "%EXTRACTED%\.env" "%PROJECT_DIR%\.env" >nul
    echo       .env restored
)

REM ── Restore cache ────────────────────────────────────────────────
if exist "%EXTRACTED%\nepse_home" (
    echo [4/4] Restoring cache and settings...
    if exist "%USERPROFILE%\.nepse" rmdir /S /Q "%USERPROFILE%\.nepse"
    xcopy /E /I /Q "%EXTRACTED%\nepse_home" "%USERPROFILE%\.nepse" >nul
    echo       Cache restored
)

REM ── Cleanup ──────────────────────────────────────────────────────
rmdir /S /Q "%TEMP_DIR%" 2>nul

echo ========================================================
echo   Restore complete!
echo ========================================================
echo   Restart the application to pick up the restored data.
echo     launcher\run_app.bat
echo.
pause
