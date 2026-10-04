import pytest

from src.alerts import engine


@pytest.fixture
def result_payload():
    return {
        "signal": "BUY",
        "confidence": 95,
        "score": 7,
        "best_rr": 3.2,
        "volume_signal": "VOLUME_SPIKE",
        "relative_volume": 2.1,
        "pattern_type": "Bullish",
        "pattern": "Bullish Engulfing",
        "trend": "UPTREND",
        "price": 110.0,
        "target1": 115.0,
        "target2": 120.0,
        "target3": 125.0,
        "milestones": {"target1": False, "target2": False, "target3": False},
    }


def test_process_alerts_returns_initial_alerts(monkeypatch, result_payload):
    # Sprint 11.2: process_alerts preloads the history once and threads it
    # through get_last_state/update_state as an optional keyword, so the
    # mocks must accept the history argument.
    monkeypatch.setattr(engine, "get_last_state", lambda symbol, history=None: None)
    monkeypatch.setattr(
        engine, "update_state", lambda symbol, result, history=None, save=True: None
    )

    alerts = engine.process_alerts("NABIL", result_payload)

    assert alerts[0]["type"] == "INITIAL"
    assert alerts[0]["priority"] == 1


def test_process_alerts_generates_change_alerts(monkeypatch, result_payload):
    previous = {
        "signal": "HOLD",
        "confidence": 80,
        "score": 3,
        "trend": "DOWNTREND",
        "volume_signal": "NORMAL",
        "milestones": {"target1": False, "target2": False, "target3": False},
    }
    monkeypatch.setattr(engine, "get_last_state", lambda symbol, history=None: previous)
    monkeypatch.setattr(
        engine, "update_state", lambda symbol, result, history=None, save=True: None
    )

    alerts = engine.process_alerts("NABIL", result_payload)

    assert any(alert["type"] == "SIGNAL_CHANGE" for alert in alerts)
    assert any(alert["type"] == "CONFIDENCE_UPGRADE" for alert in alerts)
    assert any(alert["type"] == "SCORE_UPGRADE" for alert in alerts)
    assert any(alert["type"] == "TREND_CHANGE" for alert in alerts)
    assert any(alert["type"] == "VOLUME_SPIKE" for alert in alerts)
