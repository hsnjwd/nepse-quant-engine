@echo off
title Stop NEPSE Quant Engine
color 0C

cd /d "%~dp0"

echo.
echo ==========================================
echo      STOPPING NEPSE QUANT ENGINE
echo ==========================================
echo.

py -3 -c "import launcher_utils as lu; lu.stop_process(lu.API_PID,'API')"

py -3 -c "import launcher_utils as lu; lu.stop_process(lu.BOT_PID,'Telegram Bot')"

echo.
echo ==========================================
echo Engine stopped successfully.
echo ==========================================
echo.

pause