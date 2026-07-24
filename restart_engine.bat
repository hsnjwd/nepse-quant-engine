@echo off
title Restart NEPSE Quant Engine
color 0E

cd /d "%~dp0"

call stop_engine.bat

timeout /t 2 >nul

call start_engine.bat