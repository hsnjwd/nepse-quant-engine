# Repository Structure

## Purpose

NEPSE Quant Engine uses a **src-layout** with explicit boundaries between production code, tests, operational tooling, documentation, data and UI.

## Canonical Layout

- `src/` — all production Python packages.
- `src/api/` — FastAPI transport layer only; business logic belongs in domain/services modules.
- `src/data/` — canonical market-data boundary and DataService.
- `src/engine/` — analysis orchestration and core pipeline.
- `src/indicators/`, `src/market_structure/`, `src/signals/`, `src/scoring/` — analytical primitives.
- `src/strategies/` — strategy implementations and registry.
- `src/backtesting/` — canonical historical simulation engine.
- `src/portfolio/`, `src/risk/`, `src/paper_trading/` — trading/accounting/risk domains.
- `src/ui/` — Streamlit application pages, components and session state.
- `src/bot/` — optional Telegram integration; never the core application boundary.
- `tests/` — pytest suite, including historical sprint regression tests.
- `benchmarks/` — performance/soak/production gates; not application runtime code.
- `scripts/` — maintenance, corpus, release and verification scripts.
- `launcher/` — service/process launch infrastructure.
- `docs/` — maintained project documentation.
- `data/` — sample/governed data and runtime state; generated state is ignored.
- `sync_store/` — user synchronization state.
- `assets/` — presentation/static assets used by the Streamlit frontend.
- Repository root — only project metadata, dependency/build configuration and the Streamlit entry point `app.py`.

## Rules

1. Do not add new production modules to the repository root.
2. Do not create another `backtest`, `decision`, `scanner`, `DataService`, cache or UI implementation when a canonical package already exists.
3. Keep API routers thin: validation/transport in `src/api/`, domain logic in the appropriate service/domain package.
4. Keep generated logs, PID files, caches, benchmark artifacts, local portfolios and credentials out of Git.
5. Keep tests under `tests/`; use normal `test_*.py` discovery rather than ad-hoc executable test files inside `src/`.
6. Put operational scripts in `scripts/` and process launchers in `launcher/`.
7. Prefer moving code with import updates and regression tests over leaving compatibility duplicates.
8. When a legacy implementation is retired, remove it rather than maintaining two competing sources of truth.

## Canonical Entry Points

- Streamlit: `python -m streamlit run app.py`
- FastAPI: `python -m uvicorn src.api.main:app`
- Telegram (optional): `python -m src.bot.telegram_bot`
- Tests: `python -m pytest tests/ -q --tb=short`

## Cleanup Completed by This Restructure

- Removed the obsolete standalone `ui/` frontend.
- Removed deprecated `src/test_*.py` smoke scripts.
- Removed duplicate root launcher batch files and the old root `launcher_utils.py`.
- Removed the empty accidental root artifacts `python` and `rolling_volume_mean`.
- Kept the existing `launcher/` package as the service-launch boundary.
- Kept the established `tests/test_sprint*.py` regression suite in its existing location so its repository-root assumptions remain valid.
