from pathlib import Path
from datetime import datetime

from src.utils.json_store import load_json, save_json, update_json


HISTORY_FILE = Path("data/alerts/history.json")


def load_history():
    """
    Load previous alert history.

    Tolerates a corrupt/truncated history file (e.g. a crash mid-write):
    the damaged file is renamed aside and an empty history is returned so
    the alerts pipeline never crashes the caller (scanner, API, UI).
    """
    data = load_json(HISTORY_FILE, {}, log_name="AlertsHistory")
    if not isinstance(data, dict):
        # A valid-but-wrong-shape file (e.g. a list) must not crash
        # ``get_last_state`` which calls ``.get`` on the result.
        return {}
    return data


def _backup_corrupt_file() -> None:
    """Rename a damaged history file aside so it can be inspected."""
    from src.utils.json_store import _backup

    _backup(HISTORY_FILE, ".corrupt.bak", "AlertsHistory")


def save_history(history):
    """
    Save alert history.

    Written atomically (temp file + rename) so a crash mid-write can never
    truncate ``history.json`` and break the next reader.
    """
    save_json(HISTORY_FILE, history, log_name="AlertsHistory")




def get_last_state(symbol, history=None):

    if history is None:
        history = load_history()

    state = history.get(symbol)

    if state is None:
        return None

    state.setdefault(
        "milestones",
        {
            "target1": False,
            "target2": False,
            "target3": False,
            "stoploss": False,
            "buy_zone": False,
            "breakout": False,
            "breakdown": False,
        }
    )

    return state


def _apply_state_update(symbol, result, history):
    """Mutate *history* for *symbol* in place (no file I/O)."""

    old = history.get(symbol, {})

    old["milestones"] = result.get(
        "milestones",
        old.get(
            "milestones",
            {
                "target1": False,
                "target2": False,
                "target3": False,
                "stoploss": False,
                "buy_zone": False,
                "breakout": False,
                "breakdown": False,
            }
        )
    )

    history.setdefault(symbol, {})

    history[symbol].update({
        "signal": result["signal"],
        "score": result["score"],
        "confidence": result["confidence"],
        "price": result["price"],
        "trend": result["trend"],
        "volume_signal": result["volume_signal"],
        "relative_volume": result["relative_volume"],
        "milestones": old["milestones"],
        "timestamp": datetime.now().isoformat(),

        "alert_history": history[symbol].get(
            "alert_history",
            []
        )
    })
    return history


def update_state(symbol, result, history=None, save=True):
    """Persist the latest analysis state for *symbol*.

    Args:
        symbol: Stock symbol being processed.
        result: Current analysis result.
        history: Preloaded history dict (avoids a re-read when the
            caller already holds it — Sprint 11.2).
        save: When ``False``, only mutate the in-memory *history* dict
            and skip the file write.  ``process_alert_batch`` (Sprint
            11.3) uses this to turn N per-symbol writes into a single
            atomic write at the end of a scan.

    Concurrency (Sprint 11.7): when *history* is ``None`` and the write
    is performed here, the whole read -> mutate -> write cycle runs
    under the shared cross-process lock so two uvicorn workers cannot
    lose each other's state updates.  Callers that preload *history*
    (the alert engine under its module lock) own the transaction and
    keep their single-write batching behaviour.
    """

    if history is not None:
        _apply_state_update(symbol, result, history)
        if save:
            save_history(history)
        return

    if not save:
        _apply_state_update(symbol, result, load_history())
        return

    def _mutate(h):
        return _apply_state_update(symbol, result, h)

    update_json(HISTORY_FILE, _mutate, {}, log_name="AlertsHistory")


def save_alerts(symbol, alerts):
    """Append *alerts* for *symbol* transactionally (Sprint 11.7)."""

    if not alerts:
        return

    def _mutate(history):
        history.setdefault(symbol, {})
        history[symbol].setdefault(
            "alert_history",
            []
        )
        for alert in alerts:
            history[symbol]["alert_history"].append({
                "time": datetime.now().isoformat(),
                "type": alert["type"],
                "priority": alert["priority"],
                "message": alert["message"],
            })
        return history

    update_json(HISTORY_FILE, _mutate, {}, log_name="AlertsHistory")