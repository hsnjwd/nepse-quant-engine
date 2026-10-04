@echo off
setlocal EnableExtensions
title NEPSE Quant Engine - Fix In-App Terminal
cd /d "%~dp0"

echo ============================================================
echo  NEPSE Quant Engine - In-app terminal repair
echo ============================================================
echo.

if /i "%~1"=="diagnose" goto :diagnose

echo [1/2] Running diagnostic...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_inapp_terminal.ps1"
echo.

echo [2/2] Auto-detecting bash.exe and creating the junction...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply_inapp_terminal_fix.ps1"
set "RC=%ERRORLEVEL%"
echo.

if "%RC%"=="0" (
    echo DONE: the in-app terminal should now work.
    echo Fully quit and restart the Freebuff desktop app, then retry.
) else (
    echo The automatic fix could not be applied.
    echo See the messages above, or re-run with:  fix_inapp_terminal.bat diagnose
)
echo.
pause
endlocal & exit /b %RC%

:diagnose
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_inapp_terminal.ps1"
echo.
pause
endlocal & exit /b 0
