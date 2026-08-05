# API Guide

The REST API (FastAPI) exposes the whole platform for web and mobile
clients. Run it with:

```bash
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Interactive docs are available at `http://localhost:8000/docs`
(OpenAPI), and a JSON schema at `http://localhost:8000/openapi.json`.

## Conventions

- Every response uses a stable envelope:
  `{"success": true, "data": ...}`.
- List endpoints are paginated: `page`, `page_size` (1–100), and return
  `items`, `total`, `total_pages`, `has_next`, `has_prev`.
- Errors use standard HTTP status codes:
  - `400` — invalid payload / missing required field
  - `404` — resource not found
  - `422` — query/path validation failure
  - `503` — live market data unavailable
- All market data flows through `DataService`; no endpoint talks to a
  provider directly.

## Endpoints

### Market & Analysis (original)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Health check |
| GET | `/analyze/{symbol}` | Full analysis (signal, score, indicators) |
| GET | `/backtest/{symbol}` | Backtest a symbol |
| GET | `/market/` | Market summary |
| GET | `/market/top10`, `/market/buylist`, `/market/selllist`, `/market/strongbuy` | Scans |
| GET | `/regime/` | Market regime |
| GET | `/portfolio/` | Portfolio state |
| GET | `/simulation/` | Monte Carlo simulation |
| GET/POST/DELETE | `/watchlist*` | Watchlist management |

### Stocks (Sprint 8)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/stocks/` | Paginated live quotes |
| GET | `/stocks/{symbol}` | Single quote (case-insensitive) |
| GET | `/stocks/{symbol}/history?days=` | OHLCV history (30–1000 days) |

### Signals (Sprint 8)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/signals/?signal=&min_confidence=&page=&page_size=` | Latest scan signals |
| POST | `/signals/explain` | Natural-language explanation for a signal |

### Strategies (Sprint 8)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/strategies/` | Marketplace metadata |
| GET | `/strategies/registered` | Globally registered strategies |
| POST | `/strategies/build` | Build a rule-based strategy from JSON |

### ML Models (Sprint 8)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/models/` | Available + stored models |
| POST | `/models/train` | Train on a symbol's history |
| POST | `/models/predict` | Predict the next signal |

### Optimizer (Sprint 8)

| Method | Path | Payload highlights |
| --- | --- | --- |
| POST | `/optimizer/mpt` | `returns_df` or `returns`, `objective` |
| POST | `/optimizer/risk-parity` | `returns_df` or `returns` |
| POST | `/optimizer/kelly` | `win_rate`/`avg_win`/`avg_loss` or `trades` |
| POST | `/optimizer/genetic` | `param_space`, `fitness_fn` (`module:func`) |

### Risk (Sprint 8)

| Method | Path | Payload highlights |
| --- | --- | --- |
| POST | `/risk/var` | `returns`, `confidence`, `method` |
| POST | `/risk/monte-carlo` | `returns`, `simulations`, `horizon` |
| POST | `/risk/stress-test` | `returns`, `portfolio_value` |

### Replay (Sprint 8)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/replay/start` | Start a session (`symbol`, `days`) |
| POST | `/replay/{id}/step?steps=` | Advance the session |
| GET | `/replay/{id}/state` | Current state |
| POST | `/replay/{id}/speed?speed=` | Change playback speed |

## Example

```bash
# Get live quotes (page 1, 10 per page)
curl "http://localhost:8000/stocks/?page=1&page_size=10"

# Explain a BUY signal
curl -X POST http://localhost:8000/signals/explain \
  -H "Content-Type: application/json" \
  -d '{"symbol": "NABIL", "signal": "BUY", "confidence": 85.0}'

# Train a model
curl -X POST http://localhost:8000/models/train \
  -H "Content-Type: application/json" \
  -d '{"symbol": "NABIL", "model_name": "numpy_logistic"}'
```

## Mobile Readiness

- Stable JSON schemas via the `ok()` / `paginate()` helpers in
  `src/api/schemas.py`.
- Pagination on every list endpoint.
- Authentication-ready: endpoints are intentionally unauthenticated in
  this release; wrap with an auth dependency before production exposure.
