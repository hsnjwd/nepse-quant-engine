"""Sprint 11.1 — Persistence hardening tests.

Covers the three mutable JSON state stores (alert history, portfolio
holdings, watchlist) for:

- missing file
- empty file
- malformed JSON
- truncated JSON
- atomic save (no partial state on write)
- repeated save/load round-trips

Every test monkeypatches the module-level file constant so real user
state under ``data/`` / ``~/.nepse`` is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.alerts import history as alerts_history
from src.portfolio import holdings as portfolio_holdings
from src.utils import json_store
from src.watchlist import manager as watchlist_manager


# ───────────────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────────────


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _assert_no_temp_litter(dir_path: Path) -> None:
    """Atomic writes must never leave temp files behind."""
    leftovers = [p for p in dir_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"Temp files left behind: {leftovers}"


def _assert_backup_preserved(path: Path) -> list[Path]:
    """Corrupt files must be renamed aside, preserving evidence.

    The helper names the backup ``<stem>.corrupt.bak`` (e.g.
    ``history.json`` -> ``history.corrupt.bak``) or a nanosecond-
    timestamped variant when a backup already exists, so the glob
    matches on the stem.
    """
    backups = list(path.parent.glob(f"{path.stem}*.corrupt*"))
    assert backups, "Expected a .corrupt.bak backup to be created"
    return backups


# ───────────────────────────────────────────────────────────────────
# Alerts history
# ───────────────────────────────────────────────────────────────────


class TestAlertsHistoryPersistence:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "alerts" / "history.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", self.file)

    def test_missing_file_returns_empty(self):
        assert alerts_history.load_history() == {}

    def test_empty_file_recovers_and_backs_up(self):
        _write_text(self.file, "")
        assert alerts_history.load_history() == {}
        _assert_backup_preserved(self.file)

    def test_malformed_json_recovers_and_backs_up(self):
        _write_text(self.file, '{"nabil": {broken')
        assert alerts_history.load_history() == {}
        _assert_backup_preserved(self.file)

    def test_truncated_json_recovers(self):
        _write_text(self.file, '{"NABIL": {"signal": "BUY", "score":')
        assert alerts_history.load_history() == {}
        _assert_backup_preserved(self.file)

    def test_round_trip(self):
        alerts_history.save_history({"NABIL": {"signal": "BUY"}})
        assert alerts_history.load_history() == {"NABIL": {"signal": "BUY"}}

    def test_repeated_save_load(self):
        for i in range(5):
            alerts_history.save_history({"NABIL": {"n": i}})
            assert alerts_history.load_history() == {"NABIL": {"n": i}}
        _assert_no_temp_litter(self.file.parent)

    def test_atomic_save_leaves_no_temp(self):
        alerts_history.save_history({"A": 1})
        _assert_no_temp_litter(self.file.parent)

    def test_update_state_persists(self):
        result = {
            "signal": "BUY",
            "score": 5,
            "confidence": 90,
            "price": 100.0,
            "trend": "UPTREND",
            "volume_signal": "NORMAL",
            "relative_volume": 1.0,
            "milestones": {"target1": False, "target2": False, "target3": False},
        }
        alerts_history.update_state("NABIL", result)
        state = alerts_history.get_last_state("NABIL")
        assert state is not None
        assert state["signal"] == "BUY"
        assert state["score"] == 5

    def test_corrupt_file_does_not_break_process_alerts(self):
        """A malformed history must not 500 the analyze/portfolio path."""
        from src.alerts.engine import process_alerts

        _write_text(self.file, "{corrupt")
        result = {
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
        alerts = process_alerts("NABIL", result)
        assert alerts and alerts[0]["type"] == "INITIAL"

    def test_concurrent_process_alerts_no_lost_updates(self):
        """Concurrent process_alerts must persist every symbol's state.

        Sprint 11.2 threads a single preloaded history through the
        check/update cycle under the module lock; this exercises the
        exact concurrency guarantee the parallel scanner relies on —
        distinct symbols processed from many threads must all survive,
        with no lost updates and a valid history file afterwards.
        """
        import threading

        from src.alerts.engine import process_alerts

        def _result(symbol: str, i: int) -> dict:
            # Matches the real analyze_stock payload shape (trade plan
            # always provides the targets used by check_target_alerts).
            return {
                "signal": "BUY",
                "confidence": 80 + i,
                "score": 5,
                "price": 100.0 + i,
                "trend": "UPTREND",
                "volume_signal": "NORMAL",
                "relative_volume": 1.0,
                "target1": 115.0,
                "target2": 120.0,
                "target3": 125.0,
                "milestones": {"target1": False, "target2": False, "target3": False},
            }

        errors: list[Exception] = []

        def worker(symbol: str, i: int) -> None:
            try:
                for _ in range(3):
                    process_alerts(symbol, _result(symbol, i))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"SYM{j:02d}", j))
            for j in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        history = alerts_history.load_history()
        assert len(history) == 8, "Every symbol's state must persist"
        assert all(f"SYM{j:02d}" in history for j in range(8))
        # File must still be valid JSON after the concurrent writes.
        parsed = json.loads(self.file.read_text(encoding="utf-8"))
        assert len(parsed) == 8


# ───────────────────────────────────────────────────────────────────
# Portfolio holdings
# ───────────────────────────────────────────────────────────────────


class TestPortfolioPersistence:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "portfolio.json"
        monkeypatch.setattr(portfolio_holdings, "PORTFOLIO_FILE", self.file)

    def test_missing_file_returns_empty_list(self):
        assert portfolio_holdings.load_portfolio() == []

    def test_empty_file_recovers(self):
        _write_text(self.file, "")
        assert portfolio_holdings.load_portfolio() == []
        _assert_backup_preserved(self.file)

    def test_malformed_json_recovers(self):
        _write_text(self.file, "[{broken")
        assert portfolio_holdings.load_portfolio() == []
        _assert_backup_preserved(self.file)

    def test_truncated_json_recovers(self):
        _write_text(self.file, '[{"symbol": "NABIL", "quantity"')
        assert portfolio_holdings.load_portfolio() == []
        _assert_backup_preserved(self.file)

    def test_non_list_payload_recovers(self):
        _write_text(self.file, '{"not": "a list"}')
        assert portfolio_holdings.load_portfolio() == []

    def test_save_and_load_round_trip(self):
        holdings = [{"symbol": "NABIL", "quantity": 10, "average_price": 100.0}]
        portfolio_holdings.save_portfolio(holdings)
        assert portfolio_holdings.load_portfolio() == holdings

    def test_repeated_save_load(self):
        for i in range(5):
            portfolio_holdings.save_portfolio([{"symbol": "NABIL", "n": i}])
            assert portfolio_holdings.load_portfolio() == [{"symbol": "NABIL", "n": i}]
        _assert_no_temp_litter(self.file.parent)

    def test_atomic_save_leaves_no_temp(self):
        portfolio_holdings.save_portfolio([{"symbol": "NABIL"}])
        _assert_no_temp_litter(self.file.parent)

    def test_parent_dir_created(self, monkeypatch):
        deep = self.file.parent / "nested" / "portfolio.json"
        # Use monkeypatch so the autouse fixture isolation is preserved
        # (a manual assignment + restore would clobber it).
        monkeypatch.setattr(portfolio_holdings, "PORTFOLIO_FILE", deep)
        portfolio_holdings.save_portfolio([{"symbol": "NABIL"}])
        assert deep.exists()


# ───────────────────────────────────────────────────────────────────
# Watchlist
# ───────────────────────────────────────────────────────────────────


class TestWatchlistPersistence:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.file)

    def test_missing_file_returns_empty_dict(self):
        assert watchlist_manager.load_watchlist() == {}

    def test_empty_file_recovers(self):
        _write_text(self.file, "")
        assert watchlist_manager.load_watchlist() == {}
        _assert_backup_preserved(self.file)

    def test_malformed_json_recovers(self):
        _write_text(self.file, "{nabil broken")
        assert watchlist_manager.load_watchlist() == {}
        _assert_backup_preserved(self.file)

    def test_truncated_json_recovers(self):
        _write_text(self.file, '{"NABIL": {"enabled"')
        assert watchlist_manager.load_watchlist() == {}
        _assert_backup_preserved(self.file)

    def test_non_dict_payload_recovers(self):
        _write_text(self.file, "[1, 2, 3]")
        assert watchlist_manager.load_watchlist() == {}

    def test_add_and_load_round_trip(self):
        assert watchlist_manager.add_stock("nabil") is True
        assert watchlist_manager.load_watchlist()["NABIL"]["enabled"] is True
        assert watchlist_manager.add_stock("nabil") is False

    def test_remove_round_trip(self):
        watchlist_manager.add_stock("NABIL")
        assert watchlist_manager.remove_stock("nabil") is True
        assert watchlist_manager.load_watchlist() == {}

    def test_repeated_save_load(self):
        # Sprint 12.1 (Phase 4): the raw ``save_watchlist`` writer was
        # removed — the canonical transactional bulk path (``update_json``)
        # now carries the same persistence guarantees and is exercised
        # here instead.
        from src.utils.json_store import update_json

        for i in range(5):
            update_json(
                self.file,
                lambda data, i=i: ({**data, "NABIL": {"n": i}}, True),
                {},
                log_name="Watchlist",
            )
            assert watchlist_manager.load_watchlist() == {"NABIL": {"n": i}}
        _assert_no_temp_litter(self.file.parent)

    def test_atomic_save_leaves_no_temp(self):
        from src.utils.json_store import update_json

        update_json(
            self.file,
            lambda data: ({**data, "NABIL": {"enabled": True}}, True),
            {},
            log_name="Watchlist",
        )
        _assert_no_temp_litter(self.file.parent)

    def test_deterministic_serialization(self):
        """Re-saving the same data (same insertion order) is byte-identical.

        The canonical bulk path (``update_json``) writes in insertion
        order with no silent re-sort — the same contract the removed
        raw ``save_watchlist`` held after Sprint 11.9.  Determinism
        holds for equal insertion order; the dict order is not
        re-sorted.
        """
        from src.utils.json_store import update_json

        def _set(data):
            data["B"] = 1
            data["A"] = 2
            return data, True

        update_json(self.file, _set, {}, log_name="Watchlist")
        first = self.file.read_text(encoding="utf-8")
        update_json(self.file, _set, {}, log_name="Watchlist")
        second = self.file.read_text(encoding="utf-8")
        assert first == second
        # Insertion order is preserved (no silent alphabetical re-sort).
        assert list(json.loads(self.file.read_text(encoding="utf-8")).keys()) == ["B", "A"]


# ───────────────────────────────────────────────────────────────────
# Shared helper unit tests
# ───────────────────────────────────────────────────────────────────


class TestJsonStoreHelper:
    def test_load_missing_returns_default(self, tmp_path):
        assert json_store.load_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}

    def test_save_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.json"
        json_store.save_json(target, {"x": 1})
        assert target.exists()

    def test_save_atomic_no_temp_litter(self, tmp_path):
        target = tmp_path / "s.json"
        json_store.save_json(target, {"x": 1})
        assert not list(tmp_path.glob("*.tmp"))
        assert json.loads(target.read_text(encoding="utf-8")) == {"x": 1}

    def test_malformed_backs_up_and_returns_default(self, tmp_path):
        target = tmp_path / "m.json"
        target.write_text("{oops", encoding="utf-8")
        assert json_store.load_json(target, []) == []
        assert list(tmp_path.glob("m*.corrupt*"))

    def test_non_utf8_recovers_and_backs_up(self, tmp_path):
        target = tmp_path / "bad.json"
        target.write_bytes(b"\xff\xfe\x00{broken")
        assert json_store.load_json(target, {}) == {}
        assert list(tmp_path.glob("bad*.corrupt*"))

    def test_repeated_backups_do_not_clobber(self, tmp_path):
        target = tmp_path / "m.json"
        for _ in range(3):
            target.write_text("{oops", encoding="utf-8")
            json_store.load_json(target, [])
            target.write_text("{still bad", encoding="utf-8")
        backups = list(tmp_path.glob("m*.corrupt*"))
        assert len(backups) >= 2, "Successive corruptions must all be preserved"

    def _sharing_violation(self) -> PermissionError:
        """Build a Windows sharing violation (WinError 5) for testing.

        ``PermissionError(5, ...)`` alone sets ``errno=5`` but leaves
        ``winerror=None`` (CPython only populates ``winerror`` from
        OS-level errors or the 4th constructor arg), which would not
        match ``_is_sharing_violation``.  The attribute must be set
        explicitly to simulate the real WinError 5.
        """
        err = PermissionError(5, "Access is denied")
        err.winerror = 5
        return err

    def test_transient_replace_error_retries_and_succeeds(self, tmp_path, monkeypatch):
        """A transient sharing violation (WinError 5) must be retried.

        Sprint 11.6 Phase 14 found that two uvicorn workers atomically
        replacing the same watchlist JSON concurrently can hit
        ``PermissionError`` on Windows — the destination is momentarily
        open by the other worker's reader.  ``os.replace`` is atomic so
        the retry must not corrupt state; this proves the retry loop
        absorbs transient failures and still writes the payload exactly.
        """
        import src.utils.json_store as js

        target = tmp_path / "w.json"
        real_replace = js.os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise self._sharing_violation()
            return real_replace(src, dst)

        monkeypatch.setattr(js.os, "replace", flaky_replace)
        js.save_json(target, {"A": 1, "B": 2}, sort_keys=True)

        assert calls["n"] == 3
        assert json.loads(target.read_text(encoding="utf-8")) == {"A": 1, "B": 2}
        assert not list(tmp_path.glob("*.tmp"))

    def test_persistent_replace_error_raises_after_retries(self, tmp_path, monkeypatch):
        """A permanent replace failure must surface, not loop forever."""
        import src.utils.json_store as js

        target = tmp_path / "w.json"
        calls = {"n": 0}

        def always_fail(src, dst):
            calls["n"] += 1
            raise self._sharing_violation()

        monkeypatch.setattr(js.os, "replace", always_fail)
        with pytest.raises(PermissionError):
            js.save_json(target, {"x": 1})
        assert calls["n"] == js._WRITE_RETRY_ATTEMPTS

    def test_non_sharing_oserror_raises_immediately(self, tmp_path, monkeypatch):
        """A permanent, non-sharing OSError must not be retried.

        Covers the narrowed retry branch added in Sprint 11.6: only
        WinError 5 is transient; any other OSError surfaces on the
        first attempt.
        """
        import src.utils.json_store as js

        target = tmp_path / "w.json"
        calls = {"n": 0}

        def fail_other(src, dst):
            calls["n"] += 1
            raise FileNotFoundError(2, "No such file")

        monkeypatch.setattr(js.os, "replace", fail_other)
        with pytest.raises(FileNotFoundError):
            js.save_json(target, {"x": 1})
        assert calls["n"] == 1
