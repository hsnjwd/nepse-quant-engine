"""Tests for FastAPI endpoints under src/api/."""

from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
import pytest

from src.api.main import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════
# Fake DataService objects for network-free endpoint tests
# ═══════════════════════════════════════════════════════════════════


class _FakeQuote:
    """Minimal StockQuote stand-in with the attributes serializers read."""

    symbol = "NABIL"
    company_name = "Nabil Bank"
    ltp = 500.0
    change = 5.0
    change_pct = 1.0
    open_price = 495.0
    high = 505.0
    low = 490.0
    close = 500.0
    volume = 100_000
    turnover = 50_000_000.0
    previous_close = 495.0
    timestamp = "2026-07-31T10:00:00"


class _FakeHistory:
    """Minimal StockHistory stand-in with a non-empty .df."""

    df = pd.DataFrame(
        {
            "Date": pd.date_range("2026-01-01", periods=30),
            "Open": [100.0] * 30,
            "High": [105.0] * 30,
            "Low": [95.0] * 30,
            "Close": [101.0] * 30,
            "Volume": [1000] * 30,
        }
    )


class _FakeScan:
    """Minimal MarketScanResult stand-in exposing .results."""

    results = [
        {"symbol": "NABIL", "signal": "BUY", "confidence": 80.0, "score": 7.5},
        {"symbol": "NRIC", "signal": "SELL", "confidence": 55.0, "score": 4.0},
    ]


def test_health_check_endpoint() -> None:
    """GET / returns 200 OK with system status."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "NEPSE Quant Engine Running"}


def test_analyze_endpoint_success() -> None:
    """GET /analyze/{symbol} returns 200 OK for an existing data symbol."""
    response = client.get("/analyze/nabbc")
    assert response.status_code == 200
    data = response.json()
    assert "symbol" in data
    assert data["symbol"] == "NABBC"
    assert "signal" in data


def test_analyze_endpoint_not_found() -> None:
    """GET /analyze/{symbol} returns 404 NOT FOUND when CSV is missing."""
    response = client.get("/analyze/nonexistentstock9999")
    assert response.status_code == 404
    assert "Stock data not found" in response.json()["detail"]


def test_analyze_endpoint_invalid_symbol() -> None:
    """GET /analyze/{symbol} returns 400 BAD REQUEST for path traversal formats."""
    response = client.get("/analyze/bad..symbol")
    assert response.status_code == 400


def test_backtest_endpoint_success() -> None:
    """GET /backtest/{symbol} returns 200 OK for an existing stock symbol."""
    response = client.get("/backtest/nabbc?commission=0.001&slippage=0.005")
    assert response.status_code == 200
    data = response.json()
    assert "trades" in data
    assert "metrics" in data
    assert "report" in data
    assert "symbol" in data


def test_backtest_endpoint_not_found() -> None:
    """GET /backtest/{symbol} returns 404 NOT FOUND when CSV data is missing."""
    response = client.get("/backtest/nonexistentstock9999")
    assert response.status_code == 404


def test_backtest_endpoint_negative_commission() -> None:
    """GET /backtest/{symbol} returns 400 or 422 for negative commission."""
    response = client.get("/backtest/nabbc?commission=-0.01")
    assert response.status_code in (400, 422)


def test_portfolio_endpoint() -> None:
    """GET /portfolio/ returns 200 OK."""
    response = client.get("/portfolio/")
    assert response.status_code == 200


def test_scanner_endpoints() -> None:
    """GET /market/* endpoints return status 200 when market service returns valid payloads."""
    with patch("src.api.scanner.get_market_summary", return_value={"status": "ok"}), \
         patch("src.api.scanner.get_top10", return_value=[]), \
         patch("src.api.scanner.get_buy_list", return_value=[]), \
         patch("src.api.scanner.get_sell_list", return_value=[]), \
         patch("src.api.scanner.get_strong_buy_list", return_value=[]):

        for path in ["/market/", "/market/top10", "/market/buylist", "/market/selllist", "/market/strongbuy"]:
            response = client.get(path)
            assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# Live market status endpoints — Sprint 13.1 contract
# The dashboard showed Unknown/0.00 because the market summary provider
# rejected yonepse's list-shaped payload.  These tests pin the live
# snapshot fields (index/change/change_pct/turnover/volume/status) so a
# regression to MarketSummary.empty() fails the contract loudly.
# ═══════════════════════════════════════════════════════════════════


class TestMarketStatusEndpoints:
    def test_market_status_returns_live_payload(self) -> None:
        """GET /market/status and /market-status return the live snapshot."""
        from src.data.models import MarketSummary

        fake = MarketSummary(
            index=2642.4,
            change=0.55,
            change_pct=0.02,
            volume=8701944,
            turnover=3532105518.27,
            advances=120,
            declines=80,
            unchanged=15,
            status="Closed",
        )
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_market_summary.return_value = fake
            for path in ("/market/status", "/market-status"):
                response = client.get(path)
                assert response.status_code == 200
                data = response.json()
                assert data["index"] == 2642.4
                assert data["change"] == 0.55
                assert data["change_pct"] == 0.02
                assert data["volume"] == 8701944
                assert data["turnover"] == 3532105518.27
                assert data["advances"] == 120
                assert data["declines"] == 80
                assert data["unchanged"] == 15
                assert data["status"] == "Closed"
                assert data["is_open"] is False
                assert data["timestamp"] is not None

    def test_market_status_open_market(self) -> None:
        """An Open summary serializes is_open=true."""
        from src.data.models import MarketSummary

        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_market_summary.return_value = MarketSummary(
                index=2650.0, status="Open"
            )
            response = client.get("/market-status")
        assert response.status_code == 200
        assert response.json()["is_open"] is True
        assert response.json()["status"] == "Open"

    def test_market_status_provider_failure_returns_500(self) -> None:
        """Provider failure surfaces as HTTP 500, not a silent empty summary."""
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_market_summary.side_effect = RuntimeError("boom")
            for path in ("/market/status", "/market-status"):
                response = client.get(path)
                assert response.status_code == 500
                assert "Failed to fetch market status" in response.json()["detail"]


def test_watchlist_endpoints() -> None:
    """Watchlist management GET, POST, DELETE, and scan endpoints return 200 OK."""
    # Load watchlist
    response = client.get("/watchlist")
    assert response.status_code == 200

    # Add symbol
    response = client.post("/watchlist/add/NABIL")
    assert response.status_code == 200
    assert response.json()["status"] == "added"
    assert response.json()["symbol"] == "NABIL"

    # Remove symbol
    response = client.delete("/watchlist/remove/NABIL")
    assert response.status_code == 200
    assert response.json()["status"] == "removed"
    assert response.json()["symbol"] == "NABIL"

    # Watchlist scan
    response = client.get("/watchlist/scan")
    assert response.status_code == 200


def test_watchlist_add_invalid_symbol() -> None:
    """POST /watchlist/add/{symbol} returns 400 for path traversal input."""
    response = client.post("/watchlist/add/invalid..symbol")
    assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# Sprint 8 — Stocks & Signals endpoints
# ═══════════════════════════════════════════════════════════════════


class TestStocksEndpoints:
    def test_list_stocks(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_live_market.return_value = [_FakeQuote()]
            response = client.get("/stocks/")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["symbol"] == "NABIL"

    def test_get_stock(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_stock.return_value = _FakeQuote()
            response = client.get("/stocks/NABIL")
        assert response.status_code == 200
        assert response.json()["data"]["symbol"] == "NABIL"

    def test_get_stock_not_found(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_stock.return_value = None
            response = client.get("/stocks/NABIL")
        assert response.status_code == 404

    def test_stock_history(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_history.return_value = _FakeHistory()
            response = client.get("/stocks/NABIL/history?days=30")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["symbol"] == "NABIL"
        assert data["count"] == 30


class TestSignalsEndpoints:
    def test_get_signals(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.scan_market.return_value = _FakeScan()
            response = client.get("/signals/")
        assert response.status_code == 200
        assert response.json()["data"]["total"] == 2

    def test_get_signals_filtered(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.scan_market.return_value = _FakeScan()
            response = client.get("/signals/?signal=BUY")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["signal"] == "BUY"

    def test_get_signals_min_confidence(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.scan_market.return_value = _FakeScan()
            response = client.get("/signals/?min_confidence=70")
        assert response.status_code == 200
        assert response.json()["data"]["total"] == 1

    def test_explain_signal(self) -> None:
        response = client.post(
            "/signals/explain",
            json={"symbol": "NABIL", "signal": "BUY", "confidence": 85.0},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["symbol"] == "NABIL"
        assert data["signal"] == "BUY"


# ═══════════════════════════════════════════════════════════════════
# Sprint 8 — Strategy & ML endpoints
# ═══════════════════════════════════════════════════════════════════


class TestStrategiesEndpoints:
    def test_list_strategies(self) -> None:
        response = client.get("/strategies/")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "strategies" in data
        assert data["count"] > 0

    def test_registered_strategies(self) -> None:
        response = client.get("/strategies/registered")
        assert response.status_code == 200
        data = response.json()["data"]
        assert isinstance(data, list)
        assert any(item["name"] == "MomentumStrategy" for item in data)

    def test_build_strategy(self) -> None:
        payload = {
            "name": "TestStrategy",
            "entry_rules": {
                "type": "indicator",
                "indicator": "RSI",
                "operator": ">",
                "value": 50.0,
            },
        }
        response = client.post("/strategies/build", json=payload)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "TestStrategy"
        assert data["validation"] == []

    def test_build_strategy_invalid(self) -> None:
        response = client.post("/strategies/build", json={"name": "X"})
        assert response.status_code == 400


class TestModelsEndpoints:
    def test_list_models(self) -> None:
        with patch("src.ml.model_manager.ModelManager") as mock_mgr:
            mock_mgr.return_value.list_models.return_value = []
            response = client.get("/models/")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "available" in data
        assert "random_forest" in data["available"]
        assert data["stored"] == []

    def test_train_model_missing_symbol(self) -> None:
        response = client.post("/models/train", json={})
        assert response.status_code == 400

    def test_predict_missing_symbol(self) -> None:
        response = client.post("/models/predict", json={})
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# Sprint 8 — Optimizer endpoints
# ═══════════════════════════════════════════════════════════════════


_RETURNS_PAYLOAD = {
    "returns_df": {
        "A": [0.01, -0.02, 0.015, -0.01, 0.02, -0.005, 0.01, 0.0, 0.005, 0.012],
        "B": [0.005, 0.01, -0.01, 0.02, -0.005, 0.01, 0.0, 0.015, -0.01, 0.008],
        "C": [0.02, -0.01, 0.0, 0.01, -0.015, 0.005, 0.02, -0.005, 0.01, 0.0],
    }
}


class TestOptimizerEndpoints:
    def test_mpt(self) -> None:
        response = client.post("/optimizer/mpt", json=_RETURNS_PAYLOAD)
        assert response.status_code == 200
        data = response.json()["data"]
        assert "best" in data
        assert "frontier" in data
        assert data["assets"] == ["A", "B", "C"]

    def test_mpt_missing_data(self) -> None:
        response = client.post("/optimizer/mpt", json={})
        assert response.status_code == 400

    def test_risk_parity(self) -> None:
        response = client.post("/optimizer/risk-parity", json=_RETURNS_PAYLOAD)
        assert response.status_code == 200
        data = response.json()["data"]
        assert "weights" in data
        assert abs(sum(data["weights"].values()) - 1.0) < 1e-4

    def test_kelly(self) -> None:
        response = client.post(
            "/optimizer/kelly",
            json={"win_rate": 0.6, "avg_win": 100.0, "avg_loss": 50.0},
        )
        assert response.status_code == 200
        assert response.json()["data"]["full_kelly"] > 0

    def test_kelly_from_trades(self) -> None:
        response = client.post(
            "/optimizer/kelly",
            json={"trades": [{"profit": 100.0}, {"profit": -50.0}]},
        )
        assert response.status_code == 200

    def test_genetic(self) -> None:
        payload = {
            "param_space": {"x": {"min": 0, "max": 9, "step": 1}},
            "fitness_fn": "builtins:len",
            "population_size": 8,
            "generations": 2,
        }
        response = client.post("/optimizer/genetic", json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["best"] is not None

    def test_genetic_missing_fitness(self) -> None:
        response = client.post(
            "/optimizer/genetic", json={"param_space": {"x": [1, 2, 3]}}
        )
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# Sprint 8 — Risk & Replay endpoints
# ═══════════════════════════════════════════════════════════════════


class TestRiskEndpoints:
    def test_var(self) -> None:
        payload = {
            "returns": [0.01, -0.02, 0.015, -0.01, 0.005, -0.005, 0.01] * 20
        }
        response = client.post("/risk/var", json=payload)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["var"] >= 0
        assert data["cvar"] >= 0

    def test_var_missing_returns(self) -> None:
        response = client.post("/risk/var", json={})
        assert response.status_code == 400

    def test_monte_carlo(self) -> None:
        payload = {
            "returns": [0.01, -0.02, 0.015] * 30,
            "simulations": 200,
            "horizon": 20,
        }
        response = client.post("/risk/monte-carlo", json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["simulations"] == 200

    def test_stress_test(self) -> None:
        payload = {"returns": [0.01, -0.02, 0.015] * 30}
        response = client.post("/risk/stress-test", json=payload)
        assert response.status_code == 200
        assert len(response.json()["data"]) == 5


class TestReplayEndpoints:
    def test_replay_flow(self) -> None:
        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_history.return_value = _FakeHistory()
            response = client.post(
                "/replay/start", json={"symbol": "NABIL", "days": 30}
            )
        assert response.status_code == 200
        session_id = response.json()["data"]["session_id"]

        step_resp = client.post(f"/replay/{session_id}/step", params={"steps": 3})
        assert step_resp.status_code == 200

        state_resp = client.get(f"/replay/{session_id}/state")
        assert state_resp.status_code == 200
        assert state_resp.json()["data"]["session_id"] == session_id

        speed_resp = client.post(
            f"/replay/{session_id}/speed", params={"speed": 2.0}
        )
        assert speed_resp.status_code == 200
        assert speed_resp.json()["data"]["speed"] == 2.0

    def test_replay_unknown_session(self) -> None:
        response = client.get("/replay/unknown-session/state")
        assert response.status_code == 404

    def test_replay_start_missing_symbol(self) -> None:
        response = client.post("/replay/start", json={})
        assert response.status_code == 400
