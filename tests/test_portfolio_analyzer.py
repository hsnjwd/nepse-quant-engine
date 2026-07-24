import pytest

from src.portfolio import analyzer


@pytest.fixture
def portfolio_holdings(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(analyzer, "load_portfolio", lambda: [{"symbol": "NABIL", "quantity": 10, "average_price": 100.0}])
    monkeypatch.setattr(analyzer, "portfolio_decision", lambda holding: "BUY")
    monkeypatch.setattr(analyzer, "build_advice", lambda holding: {"advice": "Hold"})

    (tmp_path / "nabil.csv").write_text("dummy", encoding="utf-8")
    return tmp_path


def test_analyze_portfolio_returns_summary(monkeypatch, portfolio_holdings):
    monkeypatch.setattr(analyzer, "analyze_stock", lambda path: {"price": 120.0})

    result = analyzer.analyze_portfolio()

    assert result["portfolio_value"] == 1200.0
    assert result["portfolio_cost"] == 1000.0
    assert result["portfolio_pnl"] == 200.0
    assert result["holdings"][0]["decision"] == "BUY"
    assert result["holdings"][0]["advisor"] == {"advice": "Hold"}
