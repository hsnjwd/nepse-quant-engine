"""Tests for FastAPI endpoints under src/api/."""

from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from src.api.main import app

client = TestClient(app)


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
