from pathlib import Path

from src.utils.json_store import load_json, update_json


WATCHLIST_FILE = Path("data/watchlist/watchlist.json")


def load_watchlist():
    """Load the watchlist, tolerating a missing/empty/corrupt file.

    A missing file yields ``{}``; an empty or malformed file is renamed
    aside (evidence preserved), logged, and ``{}`` is returned so
    callers never crash on bad state.
    """
    data = load_json(WATCHLIST_FILE, {}, log_name="Watchlist")
    if not isinstance(data, dict):
        return {}
    return data


def add_stock(symbol):
    """Add *symbol* to the watchlist transactionally.

    The read -> mutate -> write cycle runs under the shared per-file
    cross-process lock (Sprint 11.7) so two uvicorn workers can never
    lose each other's additions.  Returns ``True`` when the symbol was
    added, ``False`` when it was already present.
    """
    symbol = symbol.upper()

    def _mutate(data):
        if symbol not in data:
            data[symbol] = {"enabled": True}
            return data, True
        return data, False

    return update_json(WATCHLIST_FILE, _mutate, {}, log_name="Watchlist")


def remove_stock(symbol):
    """Remove *symbol* from the watchlist transactionally.

    Returns ``True`` when the symbol was present and removed, ``False``
    when it was not in the watchlist.
    """
    symbol = symbol.upper()

    def _mutate(data):
        if symbol in data:
            del data[symbol]
            return data, True
        return data, False

    return update_json(WATCHLIST_FILE, _mutate, {}, log_name="Watchlist")


def get_watchlist():
    return load_watchlist()
