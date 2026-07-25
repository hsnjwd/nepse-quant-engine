# NEPSE Quant Engine
![CI](https://github.com/hsnjwd/nepse-quant-engine/actions/workflows/ci.yml/badge.svg)

Open-source quantitative trading and market analysis framework for the Nepal Stock Exchange (NEPSE).

NEPSE Quant Engine is a research-oriented Python platform for analyzing listed securities, computing technical indicators, generating trading signals, simulating trade executions (backtesting), computing quantitative performance metrics, and exposing results through a lightweight FastAPI REST interface.

> This project is not a Telegram bot. It includes an optional Telegram client for monitoring and notifications, but the core system is the analysis, backtesting, and signal-generation framework.

---

## Overview

NEPSE Quant Engine brings together several building blocks commonly used in quantitative finance:

- Local CSV-based market data ingestion and validation.
- Technical indicator calculation (Moving Averages, RSI, MACD, ATR, Volume Context).
- Market structure and candlestick pattern analysis (Support/Resistance, Trend, Patterns).
- Scoring, confidence estimation, and trade-plan generation.
- **Professional Backtesting Engine**: Replays historical candles, simulates long trade executions under adverse slippage and commission, computes statistical metrics, and generates summary reports.
- **Stateful Alert System**: Tracks signal, confidence, score, trend, volume, and target changes.
- **Market Scanner**: Ranks stocks and screens for Buy, Sell, and Strong Buy signals.
- **Watchlist & Portfolio Analysis**: Manages watchlist symbols and analyzes portfolio holdings, valuation, PnL, and recommendations.
- **FastAPI REST API**: Clean, typed interface exposing analysis, backtesting, market scanner, portfolio, and watchlist capabilities.
- **Optional Telegram Bot Client**: Interactively queries the engine API.

---

## Key Features

- **Data Ingestion**: Preprocessing and loading for NEPSE market CSV files.
- **Technical Analysis**: Indicators including Moving Averages (20, 50, 180), RSI, MACD, Volume Context, and ATR Volatility.
- **Market Structure & Patterns**: Support, resistance, trend detection, and candlestick pattern scoring.
- **Signal Engine**: Scoring (0–10), confidence rating (High/Medium/Low), and trade plan generation (Entry, Stop Loss, Target 1/2/3).
- **Backtesting Module**: Replays historical candles, models execution friction (commission and slippage), tracks exit reasons (`TARGET`, `STOP_LOSS`, `SELL_SIGNAL`, `END_OF_DATA`), calculates performance metrics (win rate, profit factor, expectancy, drawdown, Sharpe ratio, CAGR), and outputs reports.
- **Market Scanner**: Screens historical data to produce Top 10 rankings, Buy Lists, Sell Lists, and Strong Buy candidates.
- **Watchlist Management**: Add, remove, load, and scan custom watchlists for active signals.
- **Stateful Alerts**: Detects initial signals and subsequent state changes across trend, volume, target, score, and confidence.
- **Portfolio Analytics**: Evaluates portfolio cost, current market value, unrealized PnL, PnL percentage, and actionable holding advice.
- **REST API**: Fully typed FastAPI interface with complete input validation and error handling.
- **Comprehensive Test Suite**: 55 automated unit and integration tests using `pytest`.

---

## Repository Structure

```text
.
├── docs/                      # Technical documentation
│   ├── ARCHITECTURE.md       # Architecture overview
│   ├── BACKTESTING.md        # Complete backtesting engine documentation
│   ├── CHANGELOG.md         # Release history
│   ├── CONTRIBUTING.md       # Contribution guidelines
│   ├── DECISIONS.md          # Architectural Decision Records (ADRs)
│   └── ROADMAP.md            # Future development roadmap
├── launcher/                  # Service launcher and process helpers
├── src/
│   ├── alerts/                # Stateful alert rules and engine
│   ├── api/                   # FastAPI REST API endpoints
│   ├── backtest/              # Backtest engine, trade simulator, metrics, and report generator
│   ├── bot/                   # Optional Telegram client implementation
│   ├── cache/                 # Caching utilities
│   ├── decision/              # Signal and decision logic
│   ├── engine/                # Core technical analysis pipeline
│   ├── indicators/            # Technical indicators (MA, RSI, MACD, Volume, Volatility)
│   ├── loaders/               # CSV data loading utilities
│   ├── logging/               # Centralized logging framework
│   ├── market_structure/      # Support, resistance, and trend heuristics
│   ├── patterns/              # Candlestick pattern detection
│   ├── portfolio/             # Portfolio valuation, PnL, and advice generator
│   ├── processors/            # Data processing pipelines
│   ├── recommendations/       # Trade plan generation
│   ├── risk/                  # Risk/reward and position sizing
│   ├── scanner/               # Market scanning utilities
│   ├── scoring/               # Indicator scoring logic
│   ├── services/              # Market service aggregators
│   ├── signals/               # Signal scoring engine
│   ├── validators/            # Data validation utilities
│   └── watchlist/             # Watchlist management and scanning
├── data/                      # Market CSV data files
├── logs/                      # Runtime logs
├── pids/                      # Process state files
├── tests/                     # Automated test suite (55 pytest tests)
└── README.md
```

---

## FastAPI Endpoints

| Category | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| **Health** | `GET` | `/` | Retrieve engine operational status |
| **Analysis** | `GET` | `/analyze/{symbol}` | Single-stock technical analysis, signals, and alerts |
| **Backtest** | `GET` | `/backtest/{symbol}` | Execute backtest run, returns trades, metrics, and report |
| **Portfolio** | `GET` | `/portfolio/` | Portfolio holdings analysis, valuation, and PnL |
| **Scanner** | `GET` | `/market/` | Market-wide summary statistics |
| **Scanner** | `GET` | `/market/top10` | Top 10 ranked stock candidates |
| **Scanner** | `GET` | `/market/buylist` | Active `BUY` signal candidates |
| **Scanner** | `GET` | `/market/selllist` | Active `SELL` signal candidates |
| **Scanner** | `GET` | `/market/strongbuy` | High-confidence `STRONG BUY` candidates |
| **Watchlist** | `GET` | `/watchlist` | Load current watchlist symbols |
| **Watchlist** | `POST` | `/watchlist/add/{symbol}` | Add stock symbol to watchlist |
| **Watchlist** | `DELETE` | `/watchlist/remove/{symbol}` | Remove stock symbol from watchlist |
| **Watchlist** | `GET` | `/watchlist/scan` | Scan all watchlist stocks for active signals |

---

## Backtesting Module

The backtesting framework ([docs/BACKTESTING.md](docs/BACKTESTING.md)) provides historical replay and trade execution simulation:

- **Orchestration (`engine.py`)**: Replays historical candles, evaluates signals, simulates `BUY` trades, calculates statistics, and builds reports.
- **Trade Simulator (`trade_simulator.py`)**: Simulates long trade executions, models adverse entry/exit slippage and round-trip commission, and tracks explicit exit reasons (`STOP_LOSS`, `TARGET`, `SELL_SIGNAL`, `END_OF_DATA`).
- **Metrics Library (`metrics.py`)**: Calculates pure performance analytics: Win Rate, Profit Factor, Average Win/Loss, Expectancy, Maximum Drawdown, Sharpe Ratio, Total Return, and CAGR.
- **Report Generator (`report.py`)**: Converts completed trade lists and metrics into structured summary dictionaries and human-readable text overviews.

Example usage:
```python
from src.backtest.engine import run_backtest

# Run backtest with 0.1% commission and 0.5% slippage
results = run_backtest(
    csv_file="data/raw/nabbc.csv",
    commission=0.001,
    slippage=0.005,
)

print(results["report"]["summary"])
```

---

## Installation

### Requirements

- Python 3.10+
- pip

### Install Dependencies

```bash
pip install -r requirements.txt
```

or install manually:

```bash
pip install fastapi uvicorn requests python-dotenv python-telegram-bot pandas pytest
```

### Environment Variables

Create a `.env` file in the project root if you plan to use the Telegram client or custom settings:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
API_BASE_URL=http://127.0.0.1:8000
DATA_DIRECTORY=data/raw
```

---

## Running Locally

### 1. Start the API Server

Start the FastAPI server using `uvicorn`:

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Or run the batch launcher on Windows:

```cmd
start_engine.bat
```

### 2. Run the Test Suite

Run the full automated test suite (55 tests):

```bash
python -m pytest
```

### 3. Run the Optional Telegram Client

Set your Telegram bot token in `.env`, then run:

```bash
python -m src.bot.telegram_bot
```

---

## Roadmap

For details on planned features, upcoming enhancements, and long-term milestones, refer to the [ROADMAP.md](docs/ROADMAP.md).

---

## Contributing

Contributions are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a feature or fix branch.
3. Add or update tests where appropriate.
4. Open a pull request with a clear description of the change.

Please keep changes focused, documented, and aligned with the project’s research-oriented scope.

---

## License

This repository does not currently include an open-source license file. If you intend to reuse or redistribute the project publicly, please add an explicit open-source license before distribution.
