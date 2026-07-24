import json
from pathlib import Path


PORTFOLIO_FILE = Path("portfolio.json")


def load_portfolio():

    if not PORTFOLIO_FILE.exists():
        return []

    with open(PORTFOLIO_FILE, "r") as f:
        return json.load(f)