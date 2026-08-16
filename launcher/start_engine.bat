@echo off
title NEPSE Quant Engine Launcher

echo =====================================
echo       NEPSE QUANT ENGINE
echo =====================================
echo.

rem Run from the script's own directory (no hardcoded developer paths).
cd /d "%~dp0"

echo Launching API...
start "NEPSE API" cmd /k python launcher\api_launcher.py

timeout /t 3 >nul

echo Launching Telegram Bot...
start "Telegram Bot" cmd /k python launcher\bot_launcher.py

echo.
echo =====================================
echo Engine Started
echo =====================================
echo.
echo Close this window.
pause
