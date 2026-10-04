from pathlib import Path

from src.utils.json_store import load_json, save_json, update_json


PORTFOLIO_FILE = Path("portfolio.json")


def load_portfolio():

    data = load_json(PORTFOLIO_FILE, [], log_name="Portfolio")
    if not isinstance(data, list):
        return []
    return data


def save_portfolio(holdings):

    save_json(PORTFOLIO_FILE, holdings, log_name="Portfolio")


def update_portfolio(mutator):
    """Apply *mutator* to the portfolio transactionally (Sprint 11.7).

    Runs ``load -> mutator -> save`` under the shared cross-process
    lock so concurrent workers (uvicorn ``--workers=2``) never lose
    each other's holding updates.

    Args:
        mutator: Callable receiving the current holdings list (or ``[]``
            when missing) and returning the new list.

    Returns:
        The new holdings list (or the mutator's ``result`` when it
        returned a ``(new_state, result)`` tuple).
    """
    return update_json(PORTFOLIO_FILE, mutator, [], log_name="Portfolio")