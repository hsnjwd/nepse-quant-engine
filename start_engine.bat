@echo off
title NEPSE Quant Engine Launcher
color 0A

cd /d "%~dp0"

echo.
echo ==========================================
echo        NEPSE QUANT ENGINE
echo ==========================================
echo.

echo Starting Engine...
echo.

py -3 -c "import launcher_utils as lu; lu.print_banner(); lu.start_api();"

py -3 -c "import launcher_utils as lu; exit(0 if lu.wait_for_api() else 1)"

if errorlevel 1 (
    echo.
    echo API failed to start.
    pause
    exit /b
)

py -3 -c "import launcher_utils as lu; lu.start_bot()"

echo.
echo ==========================================
echo API          : RUNNING
echo Telegram Bot : RUNNING
echo ==========================================
echo.
echo Logs are stored in the logs folder.
echo Press any key to close this launcher.
echo (The API and Bot will continue running.)
pause >nul