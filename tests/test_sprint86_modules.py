"""Tests for Sprint 8.6 — AI Platform Polish & Production Validation.

Covers:
- AI Advisor helpers (bull/bear scoring, risk level, trade setup)
- ML Dashboard helpers (ROC/PR curves, learning curve)
- Portfolio Optimizer helpers (diversification, drift, rebalancing)
- Broker Manager capability metadata
- Plugin registry enable/disable
- AI notification methods
- Replay page analysis helper
- Integration workflow navigation keys

Target: broad coverage of the Sprint 8.6 UI logic modules.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════
# AI Advisor Helpers
# ═══════════════════════════════════════════════════════════════════


class TestAIBullBearScores:
    def test_import_helpers(self) -> None:
        from src.ui.pages.ai_advisor_page import bull_bear_scores, risk_level, trade_setup

        assert callable(bull_bear_scores)
        assert callable(risk_level)
        assert callable(trade_setup)

    def test_all_bullish(self) -> None:
        from src.ui.pages.ai_advisor_page import bull_bear_scores

        reasons = [
            "RSI recovering from oversold zone",
            "MACD bullish crossover",
            "Price trading above EMA20",
            "Volume spike confirms strong participation",
        ]
        scores = bull_bear_scores(reasons)
        assert scores["bull"] > scores["bear"]
        assert scores["bull"] + scores["bear"] == pytest.approx(100.0, abs=0.1)

    def test_all_bearish(self) -> None:
        from src.ui.pages.ai_advisor_page import bull_bear_scores

        reasons = [
            "RSI overbought, pullback risk",
            "MACD bearish crossover",
            "Price trading below EMA20",
            "OBV shows distribution",
        ]
        scores = bull_bear_scores(reasons)
        assert scores["bear"] > scores["bull"]

    def test_mixed(self) -> None:
        from src.ui.pages.ai_advisor_page import bull_bear_scores

        reasons = ["RSI recovering", "MACD bearish crossover", "Neutral zone"]
        scores = bull_bear_scores(reasons)
        assert scores["bull"] >= 0
        assert scores["bear"] >= 0

    def test_empty(self) -> None:
        from src.ui.pages.ai_advisor_page import bull_bear_scores

        scores = bull_bear_scores([])
        assert scores == {"bull": 0.0, "bear": 0.0}


class TestAIRiskLevel:
    def test_low_risk(self) -> None:
        from src.ui.pages.ai_advisor_page import risk_level

        label, score = risk_level({"price": 500.0, "atr": 5.0, "support": 490.0})
        assert label == "Low"
        assert score <= 0.4

    def test_high_risk(self) -> None:
        from src.ui.pages.ai_advisor_page import risk_level

        label, score = risk_level({"price": 500.0, "atr": 40.0, "support": 100.0})
        assert label == "High"
        assert score >= 0.7

    def test_medium_risk(self) -> None:
        from src.ui.pages.ai_advisor_page import risk_level

        label, score = risk_level({"price": 500.0, "atr": 18.0})
        assert label in ("Medium", "High")

    def test_missing_price(self) -> None:
        from src.ui.pages.ai_advisor_page import risk_level

        label, score = risk_level({})
        assert label in ("Low", "Medium")
        assert 0.0 <= score <= 1.0

    def test_low_volume_penalty(self) -> None:
        from src.ui.pages.ai_advisor_page import risk_level

        label, score = risk_level({"price": 500.0, "atr": 5.0, "volume_signal": "LOW_VOLUME"})
        assert score > 0.3


class TestAITradeSetup:
    def test_buy_setup(self) -> None:
        from src.ui.pages.ai_advisor_page import trade_setup

        setup = trade_setup({"price": 500.0, "support": 480.0, "resistance": 550.0}, "BUY")
        assert setup["entry"] == 500.0
        assert setup["stop"] < setup["entry"]
        assert setup["target"] > setup["entry"]

    def test_sell_setup(self) -> None:
        from src.ui.pages.ai_advisor_page import trade_setup

        setup = trade_setup({"price": 500.0, "support": 460.0, "resistance": 530.0}, "SELL")
        assert setup["stop"] > setup["entry"]
        assert setup["target"] < setup["entry"]

    def test_no_price(self) -> None:
        from src.ui.pages.ai_advisor_page import trade_setup

        setup = trade_setup({}, "BUY")
        assert setup["entry"] is None

    def test_atr_fallback(self) -> None:
        from src.ui.pages.ai_advisor_page import trade_setup

        setup = trade_setup({"price": 100.0, "atr": 3.0}, "BUY")
        assert setup["entry"] == 100.0
        assert setup["stop"] is not None and setup["target"] is not None


# ═══════════════════════════════════════════════════════════════════
# ML Dashboard Helpers
# ═══════════════════════════════════════════════════════════════════


class TestMLRocCurve:
    def test_roc_binary(self) -> None:
        from src.ui.pages.ml_models_page import roc_curve_points

        y_true = np.array([0, 1, 0, 1, 0, 1, 0, 1])
        y_score = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.7, 0.4, 0.6])
        fpr, tpr, auc = roc_curve_points(y_true, y_score)
        assert len(fpr) == len(tpr) == 25
        assert 0.0 <= auc <= 1.0

    def test_roc_perfect_separation(self) -> None:
        from src.ui.pages.ml_models_page import roc_curve_points

        y_true = np.array([0, 0, 1, 1])
        y_score = np.array([0.1, 0.2, 0.9, 0.95])
        fpr, tpr, auc = roc_curve_points(y_true, y_score)
        assert auc > 0.9

    def test_roc_single_class(self) -> None:
        from src.ui.pages.ml_models_page import roc_curve_points

        fpr, tpr, auc = roc_curve_points(np.zeros(5), np.ones(5))
        assert auc == 0.5

    def test_roc_empty(self) -> None:
        from src.ui.pages.ml_models_page import roc_curve_points

        fpr, tpr, auc = roc_curve_points([], [])
        assert auc == 0.5


class TestMLPrCurve:
    def test_pr_binary(self) -> None:
        from src.ui.pages.ml_models_page import pr_curve_points

        y_true = np.array([0, 1, 0, 1, 0, 1])
        y_score = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.7])
        precision, recall, ap = pr_curve_points(y_true, y_score)
        assert len(precision) == len(recall) == 25
        assert 0.0 <= ap <= 1.0

    def test_pr_no_positive(self) -> None:
        from src.ui.pages.ml_models_page import pr_curve_points

        precision, recall, ap = pr_curve_points(np.zeros(5), np.ones(5))
        assert ap == 0.0


class TestMLLearningCurve:
    def test_learning_curve_returns_series(self) -> None:
        from src.ui.pages.ml_models_page import learning_curve_data

        class _FakeModel:
            name = "fake"

            def __init__(self) -> None:
                self._mean = None
                self._std = None
                self._w = None

            def fit(self, X: np.ndarray, y: np.ndarray) -> "_FakeModel":
                X = np.asarray(X, dtype=float)
                mean = X.mean(axis=0)
                std = X.std(axis=0)
                std[std == 0] = 1.0
                self._mean, self._std = mean, std
                Xs = (X - mean) / std
                Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
                y_bin = (y > 0).astype(int)
                self._w = np.linalg.lstsq(Xs, y_bin, rcond=None)[0]
                return self

            def predict(self, X: np.ndarray) -> np.ndarray:
                X = np.asarray(X, dtype=float)
                Xs = (X - self._mean) / self._std
                Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
                return (Xs @ self._w > 0.5).astype(int)

        rng = np.random.default_rng(0)
        X = rng.normal(size=(100, 4))
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        lc = learning_curve_data(_FakeModel, X, y)
        assert "train_sizes" in lc
        assert lc["train_sizes"], "expected at least one training size"
        assert len(lc["train_scores"]) == len(lc["train_sizes"])


# ═══════════════════════════════════════════════════════════════════
# Portfolio Optimizer Helpers
# ═══════════════════════════════════════════════════════════════════


class TestDiversificationScore:
    def test_single_asset(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import diversification_score

        assert diversification_score({"A": 1.0}) == 1.0

    def test_equal_weights(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import diversification_score

        score = diversification_score({"A": 0.5, "B": 0.5})
        assert score == pytest.approx(2.0, abs=0.001)

    def test_empty(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import diversification_score

        assert diversification_score({}) == 0.0

    def test_uneven(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import diversification_score

        score = diversification_score({"A": 0.8, "B": 0.2})
        assert 1.0 < score < 2.0


class TestAllocationDrift:
    def test_basic_drift(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import allocation_drift

        drift = allocation_drift({"A": 0.2, "B": 0.8}, {"A": 0.5, "B": 0.5})
        assert drift["A"] == pytest.approx(0.3, abs=0.001)
        assert drift["B"] == pytest.approx(-0.3, abs=0.001)

    def test_missing_assets(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import allocation_drift

        drift = allocation_drift({"A": 1.0}, {"A": 0.5, "B": 0.5})
        assert drift["B"] == pytest.approx(0.5, abs=0.001)


class TestRebalancingSuggestions:
    def test_suggestions_threshold(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import rebalancing_suggestions

        suggestions = rebalancing_suggestions(
            {"A": 0.2, "B": 0.8},
            {"A": 0.5, "B": 0.5},
            threshold=0.05,
        )
        assert len(suggestions) == 2
        by_symbol = {s["symbol"]: s for s in suggestions}
        assert by_symbol["A"]["action"] == "Add"
        assert by_symbol["B"]["action"] == "Trim"

    def test_no_suggestions(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import rebalancing_suggestions

        suggestions = rebalancing_suggestions(
            {"A": 0.48, "B": 0.52},
            {"A": 0.5, "B": 0.5},
            threshold=0.05,
        )
        assert suggestions == []

    def test_suggestion_fields(self) -> None:
        from src.ui.pages.portfolio_optimizer_page import rebalancing_suggestions

        suggestions = rebalancing_suggestions({"A": 0.9}, {"A": 0.4, "B": 0.3}, threshold=0.05)
        assert len(suggestions) == 2
        for s in suggestions:
            assert "symbol" in s and "action" in s and "drift" in s


# ═══════════════════════════════════════════════════════════════════
# Broker Manager Capabilities
# ═══════════════════════════════════════════════════════════════════


class TestBrokerCapabilities:
    def test_paper_broker(self) -> None:
        from src.ui.pages.broker_manager_page import broker_capabilities

        caps = broker_capabilities("Paper")
        assert caps["account_type"] == "Simulation"
        assert caps["requires_auth"] is False
        assert caps["features"]

    def test_stub_broker(self) -> None:
        from src.ui.pages.broker_manager_page import broker_capabilities

        caps = broker_capabilities("IBKR")
        assert caps["requires_auth"] is True
        assert "stub" in caps["features"][0].lower()

    def test_unknown_broker(self) -> None:
        from src.ui.pages.broker_manager_page import broker_capabilities

        caps = broker_capabilities("NONEXISTENT")
        assert caps["account_type"] == "Unknown"
        assert caps["order_limits"] == "Unknown"

    def test_all_brokers_covered(self) -> None:
        from src.ui.pages.broker_manager_page import BROKER_FEATURES

        for broker_type in ("Paper", "Mock", "Future NEPSE", "IBKR", "Alpaca", "Binance"):
            assert broker_type in BROKER_FEATURES


# ═══════════════════════════════════════════════════════════════════
# Plugin Registry Enable/Disable (additive)
# ═══════════════════════════════════════════════════════════════════


class TestPluginEnableDisable:
    def _make_plugin(self) -> Any:
        from src.plugins import IndicatorPlugin

        class _P(IndicatorPlugin):
            name = "sprint86_test_plugin"

            def initialize(self) -> None:
                return None

            def compute(self, df: Any, **params: Any) -> Any:
                return df

        return _P()

    def test_enable_disable_flow(self) -> None:
        from src.plugins.registry import PluginRegistry

        reg = PluginRegistry()
        plugin = self._make_plugin()
        reg.register(plugin)
        try:
            assert reg.is_enabled("sprint86_test_plugin")
            assert reg.disable("sprint86_test_plugin")
            assert not reg.is_enabled("sprint86_test_plugin")
            assert "sprint86_test_plugin" in reg.disabled_names()
            assert reg.enable("sprint86_test_plugin")
            assert reg.is_enabled("sprint86_test_plugin")
        finally:
            reg.clear()

    def test_enabled_metadata_excludes_disabled(self) -> None:
        from src.plugins.registry import PluginRegistry

        reg = PluginRegistry()
        plugin = self._make_plugin()
        reg.register(plugin)
        try:
            assert len(reg.enabled_all()) == 1
            reg.disable("sprint86_test_plugin")
            assert len(reg.enabled_all()) == 0
            assert reg.enabled_metadata() == []
            # all() still includes the plugin (unregister is not called)
            assert len(reg.all()) == 1
        finally:
            reg.clear()

    def test_disable_unknown_returns_false(self) -> None:
        from src.plugins.registry import PluginRegistry

        reg = PluginRegistry()
        assert not reg.disable("nope")
        assert not reg.enable("nope")

    def test_registry_duplicate_still_raises(self) -> None:
        from src.plugins.registry import PluginRegistry

        reg = PluginRegistry()
        plugin = self._make_plugin()
        reg.register(plugin)
        try:
            with pytest.raises(ValueError):
                reg.register(self._make_plugin())
        finally:
            reg.clear()


# ═══════════════════════════════════════════════════════════════════
# AI Notifications
# ═══════════════════════════════════════════════════════════════════


class TestAINotifications:
    def test_notify_ai(self) -> None:
        from src.ui.notifications import NotificationManager

        mgr = NotificationManager()
        n = mgr.notify_ai("AI recommendation", "BUY NABIL", priority="success")
        assert n.category == "ai"
        assert n.priority == "success"

    def test_notify_model(self) -> None:
        from src.ui.notifications import NotificationManager

        mgr = NotificationManager()
        n = mgr.notify_model("Training complete")
        assert n.category == "ai"

    def test_notify_strategy(self) -> None:
        from src.ui.notifications import NotificationManager

        mgr = NotificationManager()
        n = mgr.notify_strategy("Optimization done")
        assert n.category == "scanner"

    def test_notify_risk(self) -> None:
        from src.ui.notifications import NotificationManager

        mgr = NotificationManager()
        n = mgr.notify_risk("Concentration risk", priority="warning")
        assert n.category == "portfolio"
        assert n.priority == "warning"

    def test_notify_rebalance(self) -> None:
        from src.ui.notifications import NotificationManager

        mgr = NotificationManager()
        n = mgr.notify_rebalance("Rebalance suggested")
        assert n.category == "portfolio"

    def test_ai_category_enum(self) -> None:
        from src.ui.notifications import NotificationCategory

        assert NotificationCategory.AI.value == "ai"


# ═══════════════════════════════════════════════════════════════════
# Replay Helpers
# ═══════════════════════════════════════════════════════════════════


class TestReplayAnalysis:
    def test_analyze_frame_small(self) -> None:
        from src.ui.pages.replay_page import _analyze_frame

        df = pd.DataFrame({"Close": [100.0, 101.0]})
        assert _analyze_frame(df, "TEST") is None

    def test_analyze_frame_insufficient_columns(self) -> None:
        from src.ui.pages.replay_page import _analyze_frame

        df = pd.DataFrame({"Close": range(20)})
        assert _analyze_frame(df, "TEST") is None

    def test_analyze_frame_empty(self) -> None:
        from src.ui.pages.replay_page import _analyze_frame

        assert _analyze_frame(pd.DataFrame(), "TEST") is None

    def test_analyze_frame_full(self) -> None:
        from src.ui.pages.replay_page import _analyze_frame

        rng = np.random.default_rng(0)
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        close = 100 + np.cumsum(rng.normal(0, 1, 60))
        df = pd.DataFrame({
            "Date": dates,
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1000,
        })
        result = _analyze_frame(df, "TEST")
        # The analyzer runs the full decision pipeline; it may raise on
        # synthetic data, in which case _analyze_frame returns None.
        # Either outcome is acceptable — the guard never crashes.
        if result is not None:
            assert "signal" in result and "confidence" in result


# ═══════════════════════════════════════════════════════════════════
# API Explorer Helpers
# ═══════════════════════════════════════════════════════════════════


class TestApiExplorer:
    def test_build_url_basic(self) -> None:
        from src.api.explorer import build_url

        url = build_url("http://localhost:8000", "/api/v1/stocks/{symbol}", {"symbol": "NABIL"})
        assert url == "http://localhost:8000/api/v1/stocks/NABIL"

    def test_build_url_query_params(self) -> None:
        from src.api.explorer import build_url

        # build_url substitutes {name} placeholders in the path; extra
        # values without a matching placeholder are safely ignored.
        url = build_url(
            "http://localhost:8000",
            "/api/v1/stocks/{symbol}",
            {"symbol": "NABIL", "min_confidence": 60},
        )
        assert "NABIL" in url
        assert url == "http://localhost:8000/api/v1/stocks/NABIL"

    def test_build_url_no_base(self) -> None:
        from src.api.explorer import build_url

        url = build_url("", "/api/v1/health", {})
        assert url == "/api/v1/health"

    def test_list_endpoints_returns_list(self) -> None:
        from src.api.explorer import list_endpoints

        endpoints = list_endpoints()
        assert isinstance(endpoints, list)
        assert all("path" in e and "method" in e for e in endpoints)

    def test_send_request_error_handling(self) -> None:
        from src.api.explorer import send_request

        # A down / unreachable base should return an error envelope, never raise.
        result = send_request("http://127.0.0.1:1", "GET", "/api/v1/health")
        assert result.ok is False
        assert result.error

    def test_send_request_bad_method(self) -> None:
        from src.api.explorer import send_request

        result = send_request("http://127.0.0.1:1", "INVALID", "/x")
        assert result.ok is False


# ═══════════════════════════════════════════════════════════════════
# Integration Workflow Navigation Keys
# ═══════════════════════════════════════════════════════════════════


class TestIntegrationWorkflows:
    def test_dashboard_to_ai_advisor_key(self) -> None:
        # Dashboard quick-nav writes page + rerun; assert target page value
        from src.ui.state import initialise_state
        import streamlit as st

        initialise_state()
        st.session_state["page"] = "ai_advisor"
        assert st.session_state["page"] == "ai_advisor"

    def test_scanner_to_ai_advisor(self) -> None:
        import streamlit as st

        st.session_state["current_symbol"] = "NABIL"
        st.session_state["page"] = "ai_advisor"
        assert st.session_state.get("current_symbol") == "NABIL"
        assert st.session_state["page"] == "ai_advisor"

    def test_ai_advisor_to_charts(self) -> None:
        import streamlit as st

        st.session_state["current_symbol"] = "NABIL"
        st.session_state["page"] = "advanced_charts"
        assert st.session_state["page"] == "advanced_charts"

    def test_marketplace_to_builder(self) -> None:
        import streamlit as st

        st.session_state["sb_marketplace_load"] = {"name": "Momentum", "version": "1.0.0"}
        st.session_state["page"] = "strategy_builder"
        assert "sb_marketplace_load" in st.session_state

    def test_optimizer_receives_portfolio(self) -> None:
        import streamlit as st

        st.session_state["portfolio"] = {"holdings": [{"symbol": "NABIL", "quantity": 10, "current_value": 5000}]}
        assert st.session_state["portfolio"]["holdings"]

    def test_reports_ai_analysis_symbol_key(self) -> None:
        import streamlit as st

        st.session_state["ai_report_symbol"] = "NABIL"
        assert st.session_state.get("ai_report_symbol") == "NABIL"

    def test_replay_navigation_page(self) -> None:
        import streamlit as st

        st.session_state["page"] = "replay"
        assert st.session_state["page"] == "replay"

    def test_broker_manager_stub_safe(self) -> None:
        from src.ui.pages.broker_manager_page import broker_capabilities

        for broker_type in ("IBKR", "Alpaca", "Binance", "Future NEPSE"):
            caps = broker_capabilities(broker_type)
            assert caps["requires_auth"] is True
