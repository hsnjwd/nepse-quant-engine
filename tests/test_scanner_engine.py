from pathlib import Path

import pytest

from src.scanner import engine


@pytest.fixture
def fake_files(tmp_path):
    sample = tmp_path / "sample.csv"
    sample.write_text("dummy", encoding="utf-8")
    stock = tmp_path / "nabil.csv"
    stock.write_text("dummy", encoding="utf-8")
    return [sample, stock]


def test_get_stock_files(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
    (tmp_path / "a.csv").write_text("a", encoding="utf-8")
    (tmp_path / "b.csv").write_text("b", encoding="utf-8")

    files = engine.get_stock_files()

    assert [file.name for file in files] == ["a.csv", "b.csv"]


def test_scan_market_collects_results_and_skips_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
    (tmp_path / "nabil.csv").write_text("nabil", encoding="utf-8")
    (tmp_path / "sample.csv").write_text("sample", encoding="utf-8")

    # Redirect alert history so the scan-level alert batch (Sprint 11.3)
    # never touches the real data/alerts/history.json.
    from src.alerts import history as alerts_history

    monkeypatch.setattr(
        alerts_history, "HISTORY_FILE", tmp_path / "alerts" / "history.json"
    )

    def fake_analyze_stock(path, with_alerts=True):
        if path.endswith("nabil.csv"):
            return {"score": 8, "signal": "BUY"}
        raise ValueError("bad file")

    monkeypatch.setattr(engine, "analyze_stock", fake_analyze_stock)

    result = engine.scan_market()

    assert result["results"][0]["symbol"] == "NABIL"
    assert result["results"][0]["score"] == 8
    assert result["skipped"] == []
