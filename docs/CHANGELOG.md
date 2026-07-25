# Changelog

All notable changes to the NEPSE Quant Engine project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.5.1] - 2026-07-25

### Added
- **Backtest Engine Completed**: Finished orchestration layer in `src/backtest/engine.py` connecting CSV data loading, signal evaluation, trade simulation, analytics calculations, and report generation.
- **Backtest Metrics**: Implemented pure quantitative performance metrics in `src/backtest/metrics.py` including win rate, profit factor, average win/loss, expectancy, max drawdown, Sharpe ratio, and CAGR.
- **Report Generator**: Added `src/backtest/report.py` to format trade execution histories and performance statistics into structured summary dictionaries and human-readable text overviews.
- **BACKTESTING.md Documentation**: Created comprehensive technical architecture and usage documentation in `docs/BACKTESTING.md`.
- **55 Automated Tests**: Expanded test suite to 55 comprehensive unit and integration tests with 100% pass rate across engine, simulator, metrics, report, API, and strategy modules.

### Changed & Improved
- **Trade Simulator Refactor**: Refactored `src/backtest/trade_simulator.py` into a professional trade execution engine featuring `ExitReason` and `TradeResult` Enums, `ExecutionConfig` dataclass, normalized `TradeRecord` modeling, position sizing (`shares`), and adverse slippage/commission calculations.
- **API Validation Improvements**: Enhanced input validation across all FastAPI endpoints in `src/api/` (`analyze`, `backtest`, `watchlist`), blocking path traversal attempts (`..`, `/`, `\\`) and bad parameter inputs with appropriate `400 Bad Request`, `404 Not Found`, and `500 Internal Server Error` HTTP status codes.
- **Ranking Engine Improvements**: Refined signal scoring, indicator weighting, and candidate ranking calculations in indicator and scoring modules.
- **Scanner Integration**: Enhanced market scanner services and endpoints (`/market/`, `/market/top10`, `/market/buylist`, `/market/selllist`, `/market/strongbuy`) for real-time candidate screening.
- **Logging Improvements**: Replaced console `print()` statements across API and engine modules with structured `logger` calls (`logger.info()`, `logger.debug()`, `logger.error()`).
- **Documentation Updates**: Updated module docstrings, type annotations, and system architecture docs.

---

## [v0.5.0] - 2026-07-20

### Added
- Portfolio analyzer module (`src/portfolio/analyzer.py`) and advice generator (`src/portfolio/advisor.py`).
- Market scanner service (`src/services/market_service.py`) for automated screening.
- Watchlist manager and scanner (`src/watchlist/manager.py`, `src/watchlist/scanner.py`).

### Changed
- Refactored REST API routers under `src/api/`.

---

## [v0.4.0] - 2026-07-10

### Added
- Core technical analysis engine (`src/engine/analyzer.py`).
- Indicator calculation libraries for Moving Averages, RSI, MACD, Volume, and Volatility (`src/indicators/`).
- Candlestick pattern detection algorithms (`src/patterns/candlestick.py`).
- Signal scoring engine (`src/signals/scorer.py`).
