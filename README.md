"# NEPSE Quant Engine

Open-source quantitative trading and market analysis framework for the Nepal Stock Exchange (NEPSE).

NEPSE Quant Engine is a research-oriented Python platform for analyzing listed securities, computing technical indicators, generating trading signals, and exposing results through a lightweight API. It is designed for local analysis, backtesting workflows, and experimentation rather than as a turnkey broker integration or fully autonomous trading system.

> This project is not a Telegram bot. It includes an optional Telegram client for monitoring and notifications, but the core system is the analysis and signal-generation framework.

## Overview

NEPSE Quant Engine brings together several building blocks commonly used in quantitative finance:

- local CSV-based market data ingestion,
- technical indicator computation,
- market structure and pattern analysis,
- scoring and signal generation,
- portfolio/risk-oriented trade planning,
- a FastAPI-based analysis interface,
- and an optional Telegram client for interacting with the engine.

The project is intentionally modular so that researchers and developers can inspect, extend, and adapt the signal pipeline for NEPSE-specific market behavior.

## Key Features

- Data loading and preprocessing for NEPSE market data stored as CSV files
- Technical indicators including moving averages, momentum, volume, and volatility measures
- Market structure analysis with support, resistance, and trend heuristics
- Candlestick pattern detection and scoring
- Signal generation and confidence estimation
- Trade-plan generation with basic risk/reward and position sizing logic
- FastAPI endpoints for analysis, market scanning, watchlist management, and portfolio summaries
- Optional Telegram client for receiving analysis results from the API

## Architecture Overview

The framework is organized around a layered pipeline:

1. Data ingestion
   - Market data is loaded from CSV files in the data directory.

2. Indicator and feature engineering
   - Technical indicators, volatility measures, and volume context are calculated.

3. Market structure and pattern analysis
   - Support/resistance levels, trend signals, and candlestick patterns are evaluated.

4. Scoring and decision logic
   - Signals are scored, confidence levels are estimated, and trade recommendations are assembled.

5. API and client surfaces
   - The core engine is exposed through FastAPI, and an optional Telegram client can query the API.

This separation makes the framework easier to test, extend, and customize for new strategies or data sources.

## Repository Structure

```text
.
├── launcher/                 # Service launcher and process helpers
├── src/
│   ├── alerts/               # Alert rules and alert processing
│   ├── api/                  # FastAPI routers and application entrypoint
│   ├── bot/                  # Optional Telegram client implementation
│   ├── decision/             # Signal and decision logic
│   ├── engine/               # Core analysis pipeline
│   ├── indicators/           # Technical indicators
│   ├── loaders/              # Data loading utilities
│   ├── market_structure/     # Trend and support/resistance logic
│   ├── patterns/             # Candlestick pattern detection
│   ├── portfolio/            # Portfolio-oriented analysis
│   ├── recommendations/      # Trade plan generation
│   ├── risk/                 # Risk and reward logic
│   ├── scanner/              # Market scanning utilities
│   ├── watchlist/            # Watchlist management and scanning
│   └── ...
├── data/                     # Market data files used by the engine
├── logs/                     # Runtime logs
├── pids/                     # Process state files
└── README.md
```

## Installation

### Requirements

- Python 3.10+
- pip

### Install dependencies

```bash
pip install fastapi uvicorn requests python-dotenv python-telegram-bot pandas
```

### Environment variables

Create a `.env` file in the project root with the following values if you plan to use the Telegram client:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
API_BASE_URL=http://127.0.0.1:8000
```

The API also expects market data files to be available under the data directory.

## Quick Start

1. Clone the repository.
2. Install the dependencies above.
3. Place your NEPSE market CSV files in the data directory.
4. Start the API.
5. Optionally start the Telegram client.

## Running the API

Start the FastAPI server from the project root:

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Once running, the API will expose endpoints for:

- `/` for health/landing information
- `/analyze/{symbol}` for single-stock analysis
- `/market` for market scanning summaries
- `/watchlist` for watchlist management
- `/portfolio` for portfolio-oriented views

Example:

```bash
curl http://127.0.0.1:8000/analyze/nabil
```

## Running the Telegram Client

The repository also includes an optional Telegram client that can query the API and present analysis results in a chat interface.

Set your Telegram bot token in `.env`, then run:

```bash
python -m src.bot.telegram_bot
```

This client is intended as a lightweight interface to the engine and is not the core product itself.

## Current Project Status

NEPSE Quant Engine is currently in active development and is best understood as an experimental research framework rather than a production-grade trading platform.

Current capabilities include:

- local CSV-driven analysis,
- technical and structural signal generation,
- API access to analysis results,
- and optional Telegram-based interaction.

It does not currently provide broker integration, live execution, or a full institutional trading stack.

## Roadmap

Planned areas of improvement include:

- stronger packaging and dependency management,
- more robust configuration and environment handling,
- expanded test coverage and CI automation,
- improved data pipeline support for larger historical datasets,
- better documentation and examples for strategy development,
- and optional support for additional data providers and execution integrations.

## Contributing

Contributions are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a feature or fix branch.
3. Add or update tests where appropriate.
4. Open a pull request with a clear description of the change.

Please keep changes focused, documented, and aligned with the project’s research-oriented scope.

## License

This repository does not currently include a license file. If you intend to reuse or redistribute the project publicly, please add an explicit open-source license before distribution.
" 
