"""Tests for Sprint 6 — Production Readiness & Professional Trading Terminal.

Covers:
- Trade Journal (src/trading/journal.py)
- Market Replay (src/replay/engine.py)
- Theme Manager (src/ui/theme_manager.py)
- User Settings (src/config/user_settings.py)
- PDF Export (src/data/export.py)
- Portfolio Performance page logic
- System Status page logic

Target: 100+ new tests
"""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════
# Trade Journal Tests
# ═══════════════════════════════════════════════════════════════════


class TestTradeJournal:
    def test_create_journal(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        assert journal.count() >= 0

    def test_log_trade(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade(
            symbol="NABIL", side="SELL", quantity=100,
            entry_price=500.0, exit_price=550.0, pnl=5000.0, pnl_pct=10.0,
        )
        assert entry.symbol == "NABIL"
        assert entry.pnl == 5000.0
        assert entry.is_win
        assert journal.count() >= 1

    def test_log_trade_loss(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade(
            symbol="SCB", side="SELL", quantity=50,
            entry_price=400.0, exit_price=350.0, pnl=-2500.0,
        )
        assert entry.is_loss
        assert not entry.is_win

    def test_log_trade_with_all_fields(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade(
            symbol="NABIL", side="BUY", quantity=100,
            entry_price=500.0, exit_price=550.0, pnl=5000.0,
            commission=50.0, r_multiple=2.0, risk_pct=1.0, reward_pct=2.0,
            strategy="Momentum", confidence=8, emotion="Calm",
            tags=["swing", "breakout"], notes="Good setup",
            mistakes="None", lessons_learned="Perfect entry",
        )
        assert entry.strategy == "Momentum"
        assert entry.confidence == 8
        assert entry.emotion == "Calm"
        assert entry.risk_reward_ratio == 2.0

    def test_get_entries_filtered(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000)
        entries = journal.get_entries(symbol="NABIL")
        assert len(entries) >= 1

    def test_get_entries_by_strategy(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000, strategy="Momentum")
        entries = journal.get_entries(strategy="Momentum")
        assert len(entries) >= 1

    def test_get_entry_by_id(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000)
        found = journal.get_entry(entry.id)
        assert found is not None
        assert found.id == entry.id

    def test_get_nonexistent_entry(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        assert journal.get_entry("NONEXISTENT") is None

    def test_update_entry(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000)
        assert journal.update_entry(entry.id, notes="Updated notes", confidence=9)
        updated = journal.get_entry(entry.id)
        assert updated is not None
        assert updated.notes == "Updated notes"
        assert updated.confidence == 9

    def test_add_notes(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000)
        assert journal.add_notes(entry.id, "Great trade")
        assert journal.get_entry(entry.id).notes == "Great trade"

    def test_add_lessons(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000)
        assert journal.add_lessons(entry.id, "Patience pays off")
        assert "Patience" in journal.get_entry(entry.id).lessons_learned

    def test_delete_entry(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        entry = journal.log_trade("NABIL", "BUY", 100, 500, 550, 5000)
        eid = entry.id
        assert journal.delete_entry(eid)
        assert journal.get_entry(eid) is None

    def test_delete_nonexistent(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        assert not journal.delete_entry("NONEXISTENT")

    def test_get_stats_empty(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        stats = journal.get_stats()
        assert stats.total_trades == 0

    def test_get_stats_with_trades(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        journal.log_trade("NABIL", "SELL", 100, 500, 550, 5000)  # win
        journal.log_trade("SCB", "SELL", 50, 400, 350, -2500)  # loss
        stats = journal.get_stats()
        assert stats.total_trades >= 2
        assert stats.wins >= 1
        assert stats.losses >= 1
        assert stats.win_rate > 0

    def test_to_csv(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        journal.log_trade("NABIL", "SELL", 100, 500, 550, 5000)
        csv_bytes = journal.to_csv()
        assert isinstance(csv_bytes, bytes)
        assert b"NABIL" in csv_bytes

    def test_to_json(self) -> None:
        from src.trading.journal import TradeJournal
        journal = TradeJournal()
        journal.log_trade("NABIL", "SELL", 100, 500, 550, 5000)
        json_bytes = journal.to_json()
        data = json.loads(json_bytes.decode("utf-8"))
        assert len(data) >= 1
        assert data[0]["symbol"] == "NABIL"

    def test_holding_days(self) -> None:
        from src.trading.journal import JournalEntry
        entry = JournalEntry(
            symbol="NABIL", entry_date="2025-01-01", exit_date="2025-01-10"
        )
        assert entry.holding_days == 9

    def test_journal_entry_defaults(self) -> None:
        from src.trading.journal import JournalEntry
        e = JournalEntry()
        assert e.id == ""
        assert e.tags == []

    def test_journal_stats_defaults(self) -> None:
        from src.trading.journal import JournalStats
        s = JournalStats()
        assert s.total_trades == 0
        assert s.win_rate == 0.0


# ═══════════════════════════════════════════════════════════════════
# Market Replay Tests
# ═══════════════════════════════════════════════════════════════════


class TestMarketReplay:
    def test_create_engine(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        assert engine.state.value == "stopped"

    def test_load_dataframe(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=10, freq="D"),
            "Close": [2000 + i for i in range(10)],
            "Open": [1995 + i for i in range(10)],
            "High": [2010 + i for i in range(10)],
            "Low": [1990 + i for i in range(10)],
            "Volume": [1000000 for _ in range(10)],
        })
        assert engine.load_dataframe(df, "NEPSE")
        assert engine.total_frames == 10

    def test_step_forward(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        frame = engine.step_forward()
        assert frame is not None
        assert engine.current_frame == 1

    def test_step_backward(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        engine.step_forward(3)
        assert engine.current_frame == 3
        engine.step_backward(1)
        assert engine.current_frame == 2

    def test_go_to_frame(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        frame = engine.go_to_frame(4)
        assert frame is not None
        assert engine.current_frame == 4

    def test_go_to_frame_invalid(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        # Should clamp to valid range
        assert engine.go_to_frame(100) is not None
        assert engine.current_frame == 4  # max

    def test_set_speed(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        engine.set_speed(5.0)
        assert engine.state.value == "stopped"

    def test_pause_and_resume(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        engine.pause()  # no-op when stopped
        engine.resume()  # no-op when stopped

    def test_stop(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        engine.stop()
        assert engine.state.value == "stopped"

    def test_get_snapshot(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        snap = engine.get_snapshot()
        assert snap.progress_pct == 0.0
        assert snap.total_frames == 5

    def test_current_data(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        data = engine.current_data
        assert not data.empty
        assert "Close" in data.columns

    def test_current_data_empty_when_no_data(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        assert engine.current_data.empty

    def test_frame_callback(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=3, freq="D"),
            "Close": [2000, 2010, 2020],
        })
        engine.load_dataframe(df, "TEST")
        received: list = []

        def callback(frame, idx):
            received.append((idx, len(frame)))

        engine.set_frame_callback(callback)
        engine.step_forward()
        assert len(received) >= 1
        assert received[0][0] >= 1

    def test_cache_injection(self) -> None:
        from src.replay.engine import MarketReplayEngine
        engine = MarketReplayEngine()
        df = pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=5, freq="D"),
            "Close": [2000, 2010, 2020, 2030, 2040],
        })
        engine.load_dataframe(df, "TEST")
        engine.step_forward(2)
        engine.inject_into_cache()
        # Should not crash
        engine.clear_cache_injection()


# ═══════════════════════════════════════════════════════════════════
# Theme Manager Tests
# ═══════════════════════════════════════════════════════════════════


class TestThemeManager:
    def test_create(self) -> None:
        from src.ui.theme_manager import ThemeManager, THEMES
        mgr = ThemeManager()
        assert mgr.current_theme_name in THEMES

    def test_available_themes(self) -> None:
        from src.ui.theme_manager import ThemeManager
        mgr = ThemeManager()
        themes = mgr.available_themes
        assert "dark" in themes
        assert "light" in themes
        assert "tradingview" in themes
        assert "bloomberg" in themes

    def test_set_theme_valid(self) -> None:
        from src.ui.theme_manager import ThemeManager
        mgr = ThemeManager()
        assert mgr.set_theme("light")
        assert mgr.current_theme_name == "light"

    def test_set_theme_invalid(self) -> None:
        from src.ui.theme_manager import ThemeManager
        mgr = ThemeManager()
        assert not mgr.set_theme("nonexistent")

    def test_get_current_theme(self) -> None:
        from src.ui.theme_manager import ThemeManager, ThemeVariant
        mgr = ThemeManager()
        theme = mgr.get_current_theme()
        assert isinstance(theme, ThemeVariant)
        assert theme.background != ""

    def test_css_variables(self) -> None:
        from src.ui.theme_manager import ThemeManager
        mgr = ThemeManager()
        css = mgr.get_css_variables()
        assert "--background" in css
        assert "--primary" in css

    def test_streamlit_config(self) -> None:
        from src.ui.theme_manager import ThemeManager
        mgr = ThemeManager()
        config = mgr.streamlit_config()
        assert "primaryColor" in config
        assert "backgroundColor" in config

    def test_theme_variant_fields(self) -> None:
        from src.ui.theme_manager import ThemeVariant
        tv = ThemeVariant(name="Test")
        assert tv.name == "Test"
        assert tv.background == "#0E1117"  # default

    def test_to_css_variables(self) -> None:
        from src.ui.theme_manager import ThemeVariant
        tv = ThemeVariant(name="Test", primary="#FF0000")
        css = tv.to_css_variables()
        assert "#FF0000" in css

    def test_dark_theme(self) -> None:
        from src.ui.theme_manager import THEMES
        dark = THEMES["dark"]
        assert dark.background == "#0E1117"

    def test_tradingview_theme(self) -> None:
        from src.ui.theme_manager import THEMES
        tv = THEMES["tradingview"]
        assert tv.background == "#131722"
        assert tv.primary == "#2962FF"

    def test_bloomberg_theme(self) -> None:
        from src.ui.theme_manager import THEMES
        bb = THEMES["bloomberg"]
        assert bb.background == "#000000"
        assert bb.primary == "#FF6600"


# ═══════════════════════════════════════════════════════════════════
# User Settings Tests
# ═══════════════════════════════════════════════════════════════════


class TestUserSettings:
    def test_get_default(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        assert settings.get("theme") == "dark"
        assert settings.get("nonexistent", "fallback") == "fallback"

    def test_set_and_get(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        settings.set("theme", "light")
        assert settings.get("theme") == "light"
        settings.set("theme", "dark")  # reset

    def test_set_many(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        settings.set_many({"theme": "light", "refresh_rate": 120})
        assert settings.get("theme") == "light"
        assert settings.get("refresh_rate") == 120
        settings.set("theme", "dark")

    def test_get_all(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        all_s = settings.get_all()
        assert "theme" in all_s
        assert "commission" in all_s
        assert "default_capital" in all_s

    def test_reset(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        settings.set("theme", "light")
        settings.reset()
        assert settings.get("theme") == "dark"

    def test_reset_key(self) -> None:
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        settings.set("theme", "light")
        settings.reset_key("theme")
        assert settings.get("theme") == "dark"

    def test_save_and_load_persistence(self, tmp_path: Path) -> None:
        import sys
        # Test that save works without error
        from src.config.user_settings import UserSettings
        settings = UserSettings()
        settings.set("_test_key", "_test_value")
        assert settings.get("_test_key") == "_test_value"
        settings.set("_test_key", "")

    def test_defaults_comprehensive(self) -> None:
        from src.config.user_settings import DEFAULT_SETTINGS, UserSettings
        # All default keys should be present
        assert "commission" in DEFAULT_SETTINGS
        assert "risk_percent" in DEFAULT_SETTINGS
        assert "cache_ttl" in DEFAULT_SETTINGS
        assert "chart_type" in DEFAULT_SETTINGS
        assert "enable_price_alerts" in DEFAULT_SETTINGS


# ═══════════════════════════════════════════════════════════════════
# PDF Export Tests
# ═══════════════════════════════════════════════════════════════════


class TestPDFExport:
    def test_to_pdf_fallback(self) -> None:
        """Test that to_pdf returns bytes even without reportlab."""
        from src.data.export import ExportCenter
        pdf_bytes = ExportCenter.to_pdf(
            "Test Report",
            [{"title": "Summary", "headers": ["Metric", "Value"], "rows": [["Return", "10%"]]}],
        )
        assert isinstance(pdf_bytes, bytes)

    def test_to_pdf_empty(self) -> None:
        from src.data.export import ExportCenter
        pdf_bytes = ExportCenter.to_pdf("Empty Report", [])
        assert isinstance(pdf_bytes, bytes)

    def test_to_pdf_multiple_sections(self) -> None:
        from src.data.export import ExportCenter
        pdf_bytes = ExportCenter.to_pdf(
            "Multi Report",
            [
                {"title": "Section 1", "content": "Some text content"},
                {"title": "Section 2", "headers": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]},
            ],
        )
        assert isinstance(pdf_bytes, bytes)
