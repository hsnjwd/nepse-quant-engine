import json

import pytest

from src.watchlist import manager


@pytest.fixture
def watchlist_file(monkeypatch, tmp_path):
    path = tmp_path / "watchlist.json"
    monkeypatch.setattr(manager, "WATCHLIST_FILE", path)
    return path


def test_load_watchlist_returns_empty_when_missing(watchlist_file):
    assert manager.load_watchlist() == {}


def test_add_stock_and_remove_stock(watchlist_file):
    assert manager.add_stock("nabil") is True
    assert manager.load_watchlist()["NABIL"]["enabled"] is True

    assert manager.remove_stock("nabil") is True
    assert manager.load_watchlist() == {}


def test_add_stock_returns_false_for_existing_symbol(watchlist_file):
    manager.add_stock("nabil")
    assert manager.add_stock("nabil") is False
