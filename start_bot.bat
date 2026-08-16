@echo off
title NEPSE Quant Engine Launcher

rem Run from the script's own directory (no hardcoded developer paths).
cd /d "%~dp0"

echo Starting API...
start "NEPSE API" cmd /k "cd /d %~dp0 && py -3 -m uvicorn src.api.main:app --reload"

timeout /t 5 >nul

echo Starting Telegram Bot...
start "Telegram Bot" cmd /k "cd /d %~dp0 && py -3 -m src.bot.telegram_bot"

echo.
echo =============================
echo  NEPSE Quant Engine Started
echo =============================
pause
