from pathlib import Path
from datetime import datetime
import json

HISTORY_FILE = Path("data/alerts/history.json")

def load_history():
    """
    Load previous alert history.
    """

    if not HISTORY_FILE.exists():
        return {}

    with open(HISTORY_FILE, "r") as f:
        return json.load(f)


def save_history(history):
    """
    Save alert history.
    """

    HISTORY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(HISTORY_FILE, "w") as f:
        json.dump(
            history,
            f,
            indent=4
        )




def get_last_state(symbol):

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


def update_state(symbol, result):

    history = load_history()

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
    save_history(history)


def save_alerts(symbol, alerts):

    if not alerts:
        return

    history = load_history()

    if symbol not in history:
        history[symbol] = {}

    history[symbol].setdefault(
        "alert_history",
        []
    )

    for alert in alerts:

        history[symbol]["alert_history"].append({

            "time": datetime.now().isoformat(),

            "type": alert["type"],

            "priority": alert["priority"],

            "message": alert["message"]

        })


    save_history(history)