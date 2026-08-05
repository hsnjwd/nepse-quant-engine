@echo off
REM =====================================================================
REM NEPSE Quant Engine — Windows Launcher
REM =====================================================================
REM Usage:
REM   launcher\run_app.bat                  Start Streamlit frontend
REM   launcher\run_app.bat --api            Start FastAPI backend
REM   launcher\run_app.bat --bot            Start Telegram bot
REM   launcher\run_app.bat --help           Show this help
REM =====================================================================
title NEPSE Quant Engine
color 0A

cd /d "%~dp0.."

REM ── Source .env if present ─────────────────────────────────────
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "_first=%%a"
        if defined _first (
            REM Skip comment lines
            if not "!_first:~0,1!"=="#" (
                set "%%a=%%b"
            )
        )
    )
)

REM ── Set defaults for port variables ────────────────────────────
if "%STREAMLIT_PORT%"=="" set STREAMLIT_PORT=8501
if "%API_PORT%"=="" set API_PORT=8000

REM ── Help ────────────────────────────────────────────────────────
if /I "%1"=="--help" goto :help
if /I "%1"=="-h" goto :help

REM ── Check Python ────────────────────────────────────────────────
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH
    pause
    exit /b 1
)

REM ── Check dependencies ──────────────────────────────────────────
python -c "import streamlit" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] streamlit not found. Run: pip install -r requirements.txt
)

echo ========================================================
echo          NEPSE QUANT ENGINE
echo ========================================================
echo.

REM ── Mode dispatch ──────────────────────────────────────────────
if /I "%1"=="--api" goto :api
if /I "%1"=="--bot" goto :bot
goto :web

:web
echo [NEPSE] Starting Streamlit Frontend...
echo.
echo   URL:  http://localhost:%STREAMLIT_PORT%
echo.
echo   Press Ctrl+C to stop
echo ========================================================
python -m streamlit run app.py --server.port %STREAMLIT_PORT%
goto :end

:api
echo [NEPSE] Starting FastAPI Backend...
echo.
echo   URL:  http://localhost:%API_PORT%
echo.
echo   Press Ctrl+C to stop
echo ========================================================
python -m uvicorn src.api.main:app --host 0.0.0.0 --port %API_PORT%
goto :end

:bot
echo [NEPSE] Starting Telegram Bot...
echo.
if "%TELEGRAM_TOKEN%"=="" (
    echo [WARNING] TELEGRAM_TOKEN is not set.
    echo   Create a .env file or set the environment variable.
    echo.
)
python -m src.bot.telegram_bot
goto :end

:help
echo Usage: launcher\run_app.bat [MODE]
echo.
echo Modes:
echo   (no args)    Start Streamlit frontend (default)
echo   --api        Start FastAPI backend server
echo   --bot        Start Telegram bot
echo   --help       Show this help
echo.
echo Examples:
echo   launcher\run_app.bat
echo   launcher\run_app.bat --api
echo.
goto :end

:end
pause
