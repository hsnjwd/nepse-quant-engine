@echo off
title NEPSE Quant Engine Launcher

echo Starting API...
start "NEPSE API" cmd /k "cd /d C:\Users\User\Documents\nepse-quant-engine && py -3 -m uvicorn src.api.main:app --reload"

timeout /t 5 >nul

echo Starting Telegram Bot...
start "Telegram Bot" cmd /k "cd /d C:\Users\User\Documents\nepse-quant-engine && py -3 -m src.bot.telegram_bot"

echo.
echo =============================
echo  NEPSE Quant Engine Started
echo =============================
pause