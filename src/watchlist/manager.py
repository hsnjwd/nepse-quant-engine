from pathlib import Path
import json

WATCHLIST_FILE = Path("data/watchlist/watchlist.json")


def load_watchlist():

    if not WATCHLIST_FILE.exists():
        return {}

    with open(WATCHLIST_FILE, "r") as f:
        return json.load(f)


def save_watchlist(data):

    WATCHLIST_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(WATCHLIST_FILE, "w") as f:
        json.dump(
            data,
            f,
            indent=4
        )


def add_stock(symbol):

    data = load_watchlist()

    symbol = symbol.upper()

    if symbol not in data:

        data[symbol] = {
            "enabled": True
        }

        save_watchlist(data)

        return True

    return False


def remove_stock(symbol):

    data = load_watchlist()

    symbol = symbol.upper()

    if symbol in data:

        del data[symbol]

        save_watchlist(data)

        return True

    return False


def get_watchlist():

    return load_watchlist()




