# AI Platform Guide

The AI platform turns the NEPSE Quant Engine into a professional, AI-powered
quantitative research system. Every module is modular, extensible, testable,
and integrates through the centralized `DataService`.

## Architecture

```
                     ┌─────────────────────────────┐
                     │        DataService          │
                     │  (single source of truth)   │
                     └─────────────┬───────────────┘
                                   │
        ┌──────────────┬───────────┼──────────────┬─────────────┐
        │              │           │              │             │
   ┌────▼────┐   ┌─────▼────┐ ┌────▼────┐   ┌─────▼────┐   ┌────▼────┐
   │  ML      │   │  AI      │ │Strategy │   │Optimizer │   │  Risk   │
   │ Engine   │   │ Advisor  │ │Builder  │   │   MPT/GA │   │   Lab   │
   └─────────┘   └──────────┘ └─────────┘   └──────────┘   └─────────┘
        │              │           │              │              │
   ┌────▼────┐   ┌─────▼────┐ ┌────▼────┐   ┌─────▼────┐   ┌────▼────┐
   │ Plugins │   │ Brokers  │ │Market-  │   │   REST   │   │  Sync   │
   │         │   │          │ │place    │   │   API    │   │         │
   └─────────┘   └──────────┘ └─────────┘   └──────────┘   └─────────┘
```

## Modules

### 8.1 Machine Learning Engine — `src/ml/`

- **Feature engineering** (`feature_engineering.py`): RSI, MACD, EMA/SMA,
  ATR, ADX, OBV, CMF, ROC, VWAP, Bollinger Bands, momentum, volatility,
  returns, volume ratios, rolling statistics, lag features, regime proxies.
  All configurable via `FeatureConfig`.
- **Datasets** (`dataset.py`): `DatasetBuilder` produces BUY/HOLD/SELL
  classification datasets or forward-return regression datasets.
- **Models** (`models.py`): scikit-learn / XGBoost / LightGBM wrappers that
  gracefully fall back to pure-numpy baselines (`NumpyLogisticRegression`,
  `NumpyLinearRegression`) when libraries are unavailable.
- **Training** (`training.py`): end-to-end `TrainingPipeline`.
- **Evaluation** (`evaluation.py`): classification and regression metrics
  with no sklearn dependency.
- **Cross-validation** (`cross_validation.py`): time-series splits.
- **Versioning** (`versioning.py`): semantic versions, metadata store.
- **Persistence** (`model_manager.py`): pickle + metadata storage.
- **Prediction** (`prediction_engine.py`): unified predict interface.

### 8.2 AI Strategy Advisor — `src/ai/`

Rule-based natural-language generation (LLM-replaceable later):

| Module | Purpose |
| --- | --- |
| `advisor.py` | Explain any signal with reasons, risks, summary, recommendation |
| `market_summary.py` | Daily market commentary + sentiment classification |
| `trade_explainer.py` | Entry/exit decision explanations |
| `portfolio_review.py` | Portfolio health, concentration risk, suggestions |
| `daily_report.py` | Aggregated daily report |

Example:

```python
from src.ai import SignalAdvisor
explanation = SignalAdvisor().explain(
    symbol="NABIL",
    analysis={"rsi": 35.0, "macd": 1.5, "macd_signal": 0.8},
    regime="BULL",
)
print(explanation.summary)
```

### 8.3 Strategy Builder — `src/strategy_builder/`

Declarative, visual-style rule trees with AND/OR/NOT combinators, comparison
operators, indicator blocks, nested rules, JSON serialization, validation,
and instant backtest.

```python
from src.strategy_builder.rules import IndicatorRule, RuleGroup, and_rule

rule = and_rule(
    IndicatorRule("RSI", ">", 50.0),
    IndicatorRule("MACD", ">", 0.0),
)
```

### 8.4 Strategy Marketplace — `src/strategies/marketplace.py`

Auto-discovers any `BaseStrategy` subclass dropped into `src/strategies/`
— no registry edits required. New strategies include momentum, breakout,
mean reversion, trend following, gap, opening range, volume spike, AI trend.

### 8.5–8.7 Optimization — `src/optimization/`

- `mpt.py` — Modern Portfolio Theory: efficient frontier, min variance,
  max Sharpe, random portfolios.
- `risk_parity.py` — equal risk-contribution weights.
- `kelly.py` — Kelly Criterion + fractional variants.
- `genetic.py` — genetic algorithm over any parameter space with
  selection, crossover, mutation, and elite preservation.

### 8.8 Risk Lab — `src/risk/lab.py`

Monte Carlo simulations, historical/parametric VaR, CVaR / Expected
Shortfall, stress testing, probability of ruin, drawdown probability,
recovery analysis.

### 8.9 Future NEPSE Support — `src/data/market_interfaces.py`

Interfaces for intraday, margin, options, and futures are designed but not
wired to exchange connectivity, keeping the architecture ready.

### 8.10 Plugin System — `src/plugins/` + `plugins/`

Plugins for indicators, reports, strategies, alerts, exporters,
notification providers, and broker adapters, with automatic discovery.

### 8.11 Broker Layer — `src/brokers/`

`Broker` ABC + `PaperBroker`, `MockBroker`, `FutureNepseBroker` stub, and
IBKR / Binance / Alpaca adapter placeholders.

### 8.12–8.13 REST API — `src/api/`

Expanded FastAPI surface: `/stocks`, `/signals`, `/strategies`, `/models`,
`/optimizer`, `/risk`, `/replay` plus the original endpoints. Stable JSON
schemas with pagination, ready for mobile clients.

### 8.14 Cloud Sync — `src/sync/`

Provider-abstraction for syncing watchlists, alerts, settings, portfolio,
strategies, and reports. No cloud provider is hardcoded.

### 8.15 Enterprise Logging — `src/logging/enterprise.py`

Structured JSON logging with rotation and retention for audit, trade,
API, performance, and system logs.

## Streamlit Pages

| Page | Module |
| --- | --- |
| AI Advisor | `src/ui/pages/ai_advisor_page.py` |
| Strategy Builder | `src/ui/pages/strategy_builder_page.py` |
| Strategy Marketplace | `src/ui/pages/strategy_marketplace_page.py` |
| Portfolio Optimizer | `src/ui/pages/portfolio_optimizer_page.py` |
| Genetic Optimizer | `src/ui/pages/genetic_optimizer_page.py` |
| Monte Carlo Lab | `src/ui/pages/monte_carlo_lab_page.py` |
| ML Models | `src/ui/pages/ml_models_page.py` |
| Risk Dashboard | `src/ui/pages/risk_dashboard_page.py` |
| Broker Manager | `src/ui/pages/broker_manager_page.py` |
| Plugin Manager | `src/ui/pages/plugin_manager_page.py` |
| Cloud Sync | `src/ui/pages/cloud_sync_page.py` |

## Performance

- ML models are lazy-loaded — they never block the Streamlit UI during
  training.
- Background workers run training and simulation off the render path.
- Expensive computations are cached via `DataService`.
- `DataService` remains the single source of truth for market data.

## Extension Guide

To add a feature:

1. Put the domain logic in a new `src/<domain>/` module.
2. Expose it through a thin adapter (UI page, API router, or both).
3. Read all market data through `DataService`.
4. Add tests under `tests/` and document under `docs/`.
