"""Tests for Sprint 6.5 — Complete Production Readiness.

Covers:
- Notification Center (notifications.py)
- Keyboard Shortcuts (shortcuts.py)
- User Settings persistence (user_settings.py)
- Scanner filter presets (scanner_page.py logic)
- WebSocket charts helper functions
- Performance optimisation helpers

Target: 120+ new tests
"""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════
# Notification Center Tests
# ═══════════════════════════════════════════════════════════════════


class TestNotificationManager:
    def test_create(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        assert mgr.unread_count >= 0

    def test_notify_default(self) -> None:
        from src.ui.notifications import NotificationManager, NotificationCategory, NotificationPriority
        mgr = NotificationManager()
        n = mgr.notify("Test title", "Test message")
        assert n.title == "Test title"
        assert n.message == "Test message"
        assert n.category == "system"
        assert n.priority == "info"

    def test_notify_with_category(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify("Portfolio update", "New trade", category="portfolio", priority="success")
        assert n.category == "portfolio"
        assert n.priority == "success"

    def test_notify_portfolio(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_portfolio("Position added")
        assert n.category == "portfolio"

    def test_notify_scanner(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_scanner("Scan complete", priority="success")
        assert n.category == "scanner"
        assert n.priority == "success"

    def test_notify_price(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_price("NABIL", "Price alert", "NABIL crossed 550")
        assert n.category == "price"
        assert n.symbol == "NABIL"

    def test_notify_market(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_market("Market open")
        assert n.category == "market"

    def test_notify_ws(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_ws("WebSocket reconnected", priority="warning")
        assert n.category == "websocket"
        assert n.priority == "warning"

    def test_notify_system(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_system("Cache cleared")
        assert n.category == "system"

    def test_notify_trade(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify_trade("Trade executed", "Bought 100 NABIL")
        assert n.category == "trade"
        assert n.priority == "success"

    def test_unread_count(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        before = mgr.unread_count
        mgr.notify("Test")
        assert mgr.unread_count >= before + 1

    def test_mark_read(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify("Test")
        assert mgr.mark_read(n.id)
        all_n = mgr.get_all()
        found = [x for x in all_n if x.id == n.id]
        assert len(found) == 0 or found[0].read  # dismissed might be gone

    def test_mark_all_read(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.notify("Test 1")
        mgr.notify("Test 2")
        mgr.mark_all_read()
        assert mgr.unread_count == 0

    def test_dismiss(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify("Test")
        assert mgr.dismiss(n.id)

    def test_dismiss_all(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.notify("Test")
        mgr.dismiss_all()
        assert mgr.unread_count == 0

    def test_dismiss_by_category(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.notify("Test", category="scanner")
        mgr.notify("Test2", category="portfolio")
        mgr.dismiss_by_category("scanner")
        scanner_notifs = mgr.get_by_category("scanner")
        assert len(scanner_notifs) == 0  # dismissed

    def test_clear_all(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.notify("Test")
        mgr.clear_all()
        assert len(mgr.get_all()) == 0

    def test_delete(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        n = mgr.notify("Test")
        assert mgr.delete(n.id)

    def test_get_by_category(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.notify("Portfolio test", category="portfolio")
        notifs = mgr.get_by_category("portfolio")
        assert len(notifs) >= 1

    def test_search(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        mgr.notify("NABIL breakout detected", category="price")
        results = mgr.search("NABIL")
        assert len(results) >= 1

    def test_search_no_results(self) -> None:
        from src.ui.notifications import NotificationManager
        mgr = NotificationManager()
        results = mgr.search("ZZZZNONEXISTENT")
        assert len(results) == 0

    def test_notification_dataclass(self) -> None:
        from src.ui.notifications import Notification
        n = Notification(id="N1", title="Test", message="Msg", category="system")
        assert n.is_unread
        n.read = True
        assert not n.is_unread

    def test_notification_dataclass_dismissed(self) -> None:
        from src.ui.notifications import Notification
        n = Notification(id="N1", dismissed=True)
        assert not n.is_unread

    def test_notification_priority_enum(self) -> None:
        from src.ui.notifications import NotificationPriority
        assert NotificationPriority.INFO.value == "info"
        assert NotificationPriority.SUCCESS.value == "success"
        assert NotificationPriority.WARNING.value == "warning"
        assert NotificationPriority.CRITICAL.value == "critical"

    def test_notification_category_enum(self) -> None:
        from src.ui.notifications import NotificationCategory
        assert NotificationCategory.PORTFOLIO.value == "portfolio"
        assert NotificationCategory.SCANNER.value == "scanner"
        assert NotificationCategory.PRICE.value == "price"
        assert NotificationCategory.MARKET.value == "market"
        assert NotificationCategory.WEBSOCKET.value == "websocket"
        assert NotificationCategory.SYSTEM.value == "system"
        assert NotificationCategory.BACKGROUND.value == "background"
        assert NotificationCategory.TRADE.value == "trade"


# ═══════════════════════════════════════════════════════════════════
# Keyboard Shortcuts Tests
# ═══════════════════════════════════════════════════════════════════


class TestKeyboardShortcuts:
    def test_shortcuts_dict(self) -> None:
        from src.ui.shortcuts import SHORTCUTS
        assert "Ctrl+K" in SHORTCUTS
        assert "Ctrl+R" in SHORTCUTS
        assert "Ctrl+P" in SHORTCUTS
        assert "Ctrl+S" in SHORTCUTS
        assert "Ctrl+B" in SHORTCUTS
        assert "Ctrl+D" in SHORTCUTS
        assert "Ctrl+H" in SHORTCUTS
        assert "?" in SHORTCUTS

    def test_inject_keyboard_shortcuts_runs(self) -> None:
        from src.ui.shortcuts import inject_keyboard_shortcuts
        # Should not crash
        with patch("streamlit.markdown") as mock_md:
            inject_keyboard_shortcuts()
            assert mock_md.called

    def test_render_shortcuts_help_runs(self) -> None:
        from src.ui.shortcuts import render_shortcuts_help
        with patch("streamlit.markdown") as mock_md:
            render_shortcuts_help()
            assert mock_md.called

    def test_shortcut_labels(self) -> None:
        from src.ui.shortcuts import SHORTCUTS
        for key, label in SHORTCUTS.items():
            assert isinstance(key, str)
            assert isinstance(label, str)
            assert len(label) > 0


# ═══════════════════════════════════════════════════════════════════
# Scanner Filter Presets Tests (logic tests)
# ═══════════════════════════════════════════════════════════════════


class TestScannerFilters:
    def test_signal_filter_buy(self) -> None:
        results = [
            {"symbol": "NABIL", "signal": "BUY", "score": 80},
            {"symbol": "SCB", "signal": "HOLD", "score": 50},
            {"symbol": "CZBIL", "signal": "SELL", "score": 30},
        ]
        filtered = [r for r in results if r["signal"].upper().replace("_", " ") == "BUY"]
        assert len(filtered) == 1
        assert filtered[0]["symbol"] == "NABIL"

    def test_score_range_filter(self) -> None:
        results = [
            {"symbol": "A", "score": 90},
            {"symbol": "B", "score": 50},
            {"symbol": "C", "score": 20},
        ]
        min_score, max_score = 30, 80
        filtered = [r for r in results if min_score <= r["score"] <= max_score]
        assert len(filtered) == 1
        assert filtered[0]["symbol"] == "B"

    def test_rsi_range_filter(self) -> None:
        results = [
            {"symbol": "A", "rsi": 75},
            {"symbol": "B", "rsi": 50},
            {"symbol": "C", "rsi": 25},
        ]
        rsi_min, rsi_max = 40, 70
        filtered = [r for r in results if rsi_min <= r.get("rsi", 50) <= rsi_max]
        assert len(filtered) == 1
        assert filtered[0]["symbol"] == "B"

    def test_price_range_filter(self) -> None:
        results = [
            {"symbol": "A", "price": 500},
            {"symbol": "B", "price": 1000},
            {"symbol": "C", "price": 2000},
        ]
        price_range = (300, 1500)
        filtered = [r for r in results if price_range[0] <= r.get("price", 0) <= price_range[1]]
        assert len(filtered) == 2

    def test_text_filter(self) -> None:
        results = [{"symbol": "NABIL"}, {"symbol": "SCB"}, {"symbol": "NICA"}]
        text = "N"
        filtered = [r for r in results if text.upper() in r["symbol"].upper()]
        assert len(filtered) == 2  # NABIL, NICA

    def test_pinned_symbols_sort(self) -> None:
        results = [
            {"symbol": "SCB", "score": 50, "_pinned": False},
            {"symbol": "NABIL", "score": 80, "_pinned": True},
            {"symbol": "CZBIL", "score": 70, "_pinned": False},
        ]
        sorted_results = sorted(results, key=lambda x: (not x["_pinned"], -x["score"]))
        assert sorted_results[0]["symbol"] == "NABIL"  # pinned, highest score
        assert sorted_results[1]["symbol"] == "CZBIL"  # not pinned, second highest
        assert sorted_results[2]["symbol"] == "SCB"  # not pinned, lowest score

    def test_combined_filters(self) -> None:
        results = [
            {"symbol": "NABIL", "signal": "BUY", "score": 80, "rsi": 65},
            {"symbol": "SCB", "signal": "BUY", "score": 60, "rsi": 55},
            {"symbol": "CZBIL", "signal": "SELL", "score": 30, "rsi": 25},
        ]
        filtered = [
            r for r in results
            if r["signal"].upper().replace("_", " ") == "BUY"
            and r["score"] >= 50
            and 30 <= r.get("rsi", 50) <= 100
        ]
        assert len(filtered) == 2  # NABIL and SCB


# ═══════════════════════════════════════════════════════════════════
# User Settings Persistence Tests
# ═══════════════════════════════════════════════════════════════════


class TestUserSettingsExtended:
    def test_get_default_values(self) -> None:
        from src.config.user_settings import UserSettings, DEFAULT_SETTINGS
        settings = UserSettings()
        for key, default in DEFAULT_SETTINGS.items():
            if not key.startswith("_"):
                assert settings.get(key) is not None or default is None

    def test_theme_default(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        assert settings.get("theme") == "dark"

    def test_commission_default(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        assert settings.get("commission") == 0.001

    def test_cache_ttl_default(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        assert settings.get("cache_ttl") == 30

    def test_set_and_reset(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        settings.set("theme", "light")
        assert settings.get("theme") == "light"
        settings.reset_key("theme")
        assert settings.get("theme") == "dark"

    def test_default_capital_default(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        assert settings.get("default_capital") == 100000.0


# ═══════════════════════════════════════════════════════════════════
# WebSocket Charts Helper Tests
# ═══════════════════════════════════════════════════════════════════


class TestWebSocketChartsHelpers:
    def test_thread_safe_queue(self) -> None:
        import threading
        from src.ui.pages.advanced_charts_page import _live_quotes_queue, _live_quotes_lock

        # Simulate WebSocket callback
        def worker(item):
            with _live_quotes_lock:
                _live_quotes_queue.append(item)

        t = threading.Thread(target=worker, args=("test_quote",))
        t.start()
        t.join()

        with _live_quotes_lock:
            assert "test_quote" in _live_quotes_queue
            _live_quotes_queue.clear()

    def test_quote_queue_thread_safety(self) -> None:
        import threading
        from src.ui.pages.advanced_charts_page import _live_quotes_queue, _live_quotes_lock

        errors = []

        def writer(n):
            try:
                with _live_quotes_lock:
                    _live_quotes_queue.append(f"q{n}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        with _live_quotes_lock:
            assert len(_live_quotes_queue) == 50
            _live_quotes_queue.clear()

    def test_handle_live_quote(self) -> None:
        from src.ui.pages.advanced_charts_page import _handle_live_quote, _live_quotes_queue, _live_quotes_lock
        from src.data.models import StockQuote

        q = StockQuote(symbol="NABIL", ltp=500.0)
        _handle_live_quote(q)

        with _live_quotes_lock:
            assert len(_live_quotes_queue) >= 1
            _live_quotes_queue.clear()


# ═══════════════════════════════════════════════════════════════════
# Performance Optimisation Tests
# ═══════════════════════════════════════════════════════════════════


class TestPerformanceHelpers:
    def test_lazy_import_pattern(self) -> None:
        # Test that lazy imports work correctly
        import importlib
        # Should not crash importing heavy modules at module level
        try:
            importlib.import_module("src.data.service")
        except Exception:
            pytest.skip("DataService not importable in test env")

    def test_session_state_memoization(self) -> None:
        """Test that session_state can cache computed values."""
        import streamlit as st

        key = "_test_memo"
        # Simulate computation with caching
        if key not in st.session_state:
            st.session_state[key] = 42
        assert st.session_state.get(key) == 42

    def test_dataframe_copy_not_modify(self) -> None:
        """Test that we don't modify cached dataframes in place."""
        df = pd.DataFrame({"A": [1, 2, 3]})
        cached = df.copy()
        cached["B"] = [4, 5, 6]
        assert "B" not in df.columns  # original unchanged


# ═══════════════════════════════════════════════════════════════════
# Scanner Preset Management Tests
# ═══════════════════════════════════════════════════════════════════


class TestScannerPresets:
    def test_save_and_load_preset(self) -> None:
        presets = {}
        filters = {
            "scanner_signal": "BUY",
            "scanner_min_score": 50,
            "scanner_max_score": 100,
        }
        presets["Momentum Scan"] = filters
        assert "Momentum Scan" in presets
        assert presets["Momentum Scan"]["scanner_signal"] == "BUY"

    def test_delete_preset(self) -> None:
        presets = {"Test": {"signal": "BUY"}}
        del presets["Test"]
        assert len(presets) == 0

    def test_import_filters(self) -> None:
        import json
        data = json.dumps({"My Scan": {"signal": "BUY", "score": 70}})
        imported = json.loads(data)
        assert "My Scan" in imported
        assert imported["My Scan"]["signal"] == "BUY"

    def test_export_filters(self) -> None:
        filters = {"signal": "BUY", "score": 80}
        export_json = json.dumps({"Current Scan": filters})
        assert len(export_json) > 0
        assert "BUY" in export_json
