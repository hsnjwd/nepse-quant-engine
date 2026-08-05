"""Sprint 10 — Architecture Consolidation regression tests.

Covers:
- Momentum strategy fix (SMA_20/SMA_50 columns, no silent fallback)
- Confidence engine consolidation (single implementation)
- Unified market data pipeline (single CSV loader, path resolver)
- Configuration consolidation (single source of truth)
- Version consistency (canonical version module)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config import DATA_SERVICE_API_URLS, DATA_DIRECTORY
from src.data.providers import CSVProvider
from src.decision.confidence import calculate_confidence as canonical_confidence
from src.loaders.csv_loader import load_csv, resolve_stock_csv_path
from src.strategies.momentum import MomentumStrategy
from src.version import __version__


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


def _uptrend_frame(rows: int = 70, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Build a monotonically rising OHLCV frame (price above both MAs)."""
    prices = [start + i * step for i in range(rows)]
    return pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=rows, freq="D"),
            "Open": [p - 0.5 for p in prices],
            "High": [p + 1.0 for p in prices],
            "Low": [p - 1.0 for p in prices],
            "Close": prices,
            "Volume": [1_000_000 + i * 100 for i in range(rows)],
        }
    )


def _downtrend_frame(rows: int = 70, start: float = 200.0, step: float = -1.0) -> pd.DataFrame:
    """Build a falling frame (price below the long-term MA)."""
    prices = [start + i * step for i in range(rows)]
    return pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=rows, freq="D"),
            "Open": [p + 0.5 for p in prices],
            "High": [p + 1.0 for p in prices],
            "Low": [p - 1.0 for p in prices],
            "Close": prices,
            "Volume": [1_000_000 + i * 100 for i in range(rows)],
        }
    )


# ═══════════════════════════════════════════════════════════════════
# Task 1 — Momentum Strategy fix
# ═══════════════════════════════════════════════════════════════════


class TestMomentumStrategyFix:
    def test_buy_above_moving_averages(self) -> None:
        """BUY fires when RSI is strong and price is above SMA_20 >= SMA_50."""
        strategy = MomentumStrategy()
        payload = strategy.generate_signal(_uptrend_frame())
        assert payload["signal"] == "BUY"
        assert payload["details"]["ma20"] > payload["details"]["ma50"]

    def test_no_buy_below_long_term_ma(self) -> None:
        """No BUY when price is below the long-term SMA_50."""
        strategy = MomentumStrategy()
        payload = strategy.generate_signal(_downtrend_frame())
        assert payload["signal"] != "BUY"
        assert payload["details"]["ma20"] < payload["details"]["ma50"]

    def test_missing_indicator_columns_raise(self, monkeypatch) -> None:
        """Absent SMA_20/SMA_50 columns raise a clear ValueError."""
        import src.strategies.momentum as momentum_mod

        monkeypatch.setattr(momentum_mod, "add_moving_averages", lambda df: df.copy())

        strategy = MomentumStrategy()
        with pytest.raises(ValueError, match="SMA_20"):
            strategy.generate_signal(_uptrend_frame())

    def test_short_history_never_buys(self) -> None:
        """Fewer than 50 rows means SMA_50 is NaN — never BUY."""
        strategy = MomentumStrategy()
        payload = strategy.generate_signal(_uptrend_frame(rows=30))
        assert payload["signal"] != "BUY"

    def test_signal_payload_shape(self) -> None:
        """Signal payload keeps the standardized shape."""
        payload = MomentumStrategy().generate_signal(_uptrend_frame())
        assert payload["strategy_name"] == "MomentumStrategy"
        assert payload["signal"] in ("BUY", "SELL", "HOLD")
        assert "score" in payload and "details" in payload


# ═══════════════════════════════════════════════════════════════════
# Task 2 — Confidence engine consolidation
# ═══════════════════════════════════════════════════════════════════


class TestConfidenceConsolidation:
    def test_canonical_confidence_is_stable(self) -> None:
        """The canonical (analyzer-used) confidence formula is unchanged."""
        assert canonical_confidence(0, {"trend": 0, "macd": 0, "volume": 0, "pattern": 0, "rsi": 0}) == 50
        assert canonical_confidence(1, {"trend": 1, "macd": 1, "volume": 1, "pattern": 1, "rsi": 1}) == 71
        assert canonical_confidence(-10, {"trend": -5, "macd": -5, "volume": -5, "pattern": -5, "rsi": -5}) == 0

    def test_legacy_engine_is_deprecated_but_works(self) -> None:
        """src.decision.engine still works for backward compat and warns."""
        import warnings

        from src.decision import engine as legacy_engine

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            conf = legacy_engine.calculate_confidence(8)
            plan = legacy_engine.generate_trade_plan({"Close": 100.0}, 8, "BUY")

        assert conf == 80
        assert plan["signal"] == "BUY"
        assert plan["entry_zone"] == (98.0, 102.0)
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ═══════════════════════════════════════════════════════════════════
# Tasks 3-4 — Unified market data pipeline / single CSV loader
# ═══════════════════════════════════════════════════════════════════


class TestSingleCsvLoader:
    def test_csv_provider_delegates_to_canonical_loader(self, tmp_path: Path) -> None:
        """CSVProvider._load_csv produces identical output to load_csv."""
        csv_file = tmp_path / "NABIL.csv"
        csv_file.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2025-01-03,106,110,102,108,1,200,000\n"
            "2025-01-01,100,105,95,102,1,000,000\n"
            "2025-01-02,103,108,98,106,1,100,000\n",
            encoding="utf-8",
        )

        canonical = load_csv(csv_file)
        provider = CSVProvider(data_dir=tmp_path)

        # Compare on a fresh copy — provider uses its own instance loader.
        provider_df = provider._load_csv(csv_file)

        assert canonical.equals(provider_df)
        assert list(canonical["Close"]) == [102.0, 106.0, 108.0]
        assert list(canonical["Volume"]) == [1_000_000, 1_100_000, 1_200_000]

    def test_load_csv_raises_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_csv(tmp_path / "missing.csv")


class TestPathResolver:
    def test_resolves_existing_symbol(self, tmp_path: Path) -> None:
        (tmp_path / "nabil.csv").write_text("a", encoding="utf-8")
        assert resolve_stock_csv_path("NABIL", data_dir=tmp_path) == tmp_path / "nabil.csv"

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        assert resolve_stock_csv_path("NONEXISTENT", data_dir=tmp_path) is None

    def test_resolves_with_explicit_data_dir(self, tmp_path: Path) -> None:
        """Resolver honours an explicit data_dir override."""
        (tmp_path / "nabil.csv").write_text("a", encoding="utf-8")
        assert resolve_stock_csv_path("nabil", data_dir=tmp_path) is not None


# ═══════════════════════════════════════════════════════════════════
# Task 5 — Configuration consolidation
# ═══════════════════════════════════════════════════════════════════


class TestConfigConsolidation:
    def test_data_service_defaults_match_config(self) -> None:
        """DataService._default_api_urls derives from config's single source."""
        from src.data.service import DataService

        for key, value in DATA_SERVICE_API_URLS.items():
            assert DataService._default_api_urls.get(key) == value, f"key {key} diverged"

    def test_config_has_github_datasets(self) -> None:
        """github_datasets must exist (was missing from DataService defaults)."""
        assert "github_datasets" in DATA_SERVICE_API_URLS
        assert DATA_SERVICE_API_URLS["github_datasets"]


# ═══════════════════════════════════════════════════════════════════
# Task 6 — Version consistency
# ═══════════════════════════════════════════════════════════════════


class TestVersionConsistency:
    def test_version_module_matches_version_file(self) -> None:
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        assert __version__ == version_file.read_text(encoding="utf-8").strip()

    def test_api_reports_same_version(self) -> None:
        from src.api.main import app

        assert app.version == __version__

    def test_src_package_exposes_version(self) -> None:
        import src

        assert src.__version__ == __version__
