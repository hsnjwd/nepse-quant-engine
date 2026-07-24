import pandas as pd
import pytest

from src.engine import analyzer


@pytest.fixture
def patched_dependencies(monkeypatch):
    monkeypatch.setattr(analyzer, "add_moving_averages", lambda df: df)
    monkeypatch.setattr(analyzer, "add_momentum_indicators", lambda df: df)
    monkeypatch.setattr(analyzer, "add_volume_indicators", lambda df: df)
    monkeypatch.setattr(analyzer, "add_volatility_indicators", lambda df: df)
    monkeypatch.setattr(analyzer, "get_support", lambda df: 80.0)
    monkeypatch.setattr(analyzer, "get_resistance", lambda df: 120.0)
    monkeypatch.setattr(analyzer, "get_trend", lambda latest: "UPTREND")
    monkeypatch.setattr(analyzer, "detect_pattern", lambda df: {"name": "Bullish", "type": "Bullish", "strength": "Strong", "score": 2})
    monkeypatch.setattr(analyzer, "calculate_score", lambda latest, pattern: (6, {"trend": 2, "rsi": 1, "macd": 2, "volume": 1, "pattern": 2, "reasons": ["ok"]}))
    monkeypatch.setattr(analyzer, "calculate_confidence", lambda score, breakdown: 85)
    monkeypatch.setattr(analyzer, "create_trade_plan", lambda result: {"target1": 110.0, "target2": 120.0, "target3": 130.0})
    monkeypatch.setattr(analyzer, "calculate_risk_reward", lambda result: {"best_rr": 3.2})
    monkeypatch.setattr(analyzer, "calculate_position_size", lambda result: {"position_size": 10})
    monkeypatch.setattr(analyzer, "check_alerts", lambda result: [{"type": "BUY"}])
    monkeypatch.setattr(analyzer, "process_alerts", lambda symbol, result: [{"type": "INITIAL"}])
    monkeypatch.setattr(analyzer, "generate_signal", lambda result: "BUY")


def test_analyze_dataframe_returns_expected_structure(patched_dependencies, sample_frame):
    result = analyzer.analyze_dataframe(sample_frame)

    assert result["signal"] == "BUY"
    assert result["score"] == 6
    assert result["confidence"] == 85
    assert result["price"] == 100.0
    assert result["trend"] == "UPTREND"
    assert result["alerts"] == [{"type": "BUY"}]


def test_analyze_dataframe_raises_for_empty_frame(monkeypatch, sample_frame):
    monkeypatch.setattr(analyzer, "add_moving_averages", lambda df: df.iloc[0:0])
    monkeypatch.setattr(analyzer, "add_momentum_indicators", lambda df: df.iloc[0:0])
    monkeypatch.setattr(analyzer, "add_volume_indicators", lambda df: df.iloc[0:0])
    monkeypatch.setattr(analyzer, "add_volatility_indicators", lambda df: df.iloc[0:0])
    with pytest.raises(ValueError, match="No rows remaining"):
        analyzer.analyze_dataframe(sample_frame)


def test_analyze_stock_uses_file_name_and_process_alerts(monkeypatch, tmp_path):
    csv_path = tmp_path / "nabil.csv"
    csv_path.write_text("date,close\n2024-01-01,100\n", encoding="utf-8")

    monkeypatch.setattr(analyzer, "load_csv", lambda file: pd.DataFrame([{"Close": 100.0, "SMA_20": 95.0, "SMA_50": 90.0, "RSI": 55.0, "MACD": 1.5, "MACD_SIGNAL": 0.8, "VOLUME_SIGNAL": "NORMAL", "RELATIVE_VOLUME": 1.2, "VOLUME_SCORE": 1, "ATR": 2.5}]))
    monkeypatch.setattr(analyzer, "analyze_dataframe", lambda df: {"price": 100.0, "signal": "BUY"})
    monkeypatch.setattr(analyzer, "process_alerts", lambda symbol, result: [])

    result = analyzer.analyze_stock(str(csv_path))

    assert result["symbol"] == "NABIL"
    assert result["price"] == 100.0
    assert result["signal"] == "BUY"
