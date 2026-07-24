@echo off
title NEPSE Quant Engine Launcher

echo =====================================
echo       NEPSE QUANT ENGINE
echo =====================================
echo.

cd /d C:\Users\User\Documents\nepse-quant-engine

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