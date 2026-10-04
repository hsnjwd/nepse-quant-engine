"""Sprint 11.7 — Multi-worker state consistency tests.

Covers the cross-worker lost-update problem and its fix:

- ``json_store.update_json`` / ``locked_json`` transactional mutation
  (read -> mutate -> write under a cross-process lock).
- Concurrent watchlist / alert-history / portfolio mutations from many
  threads *and* from separate OS processes (simulating uvicorn
  ``--workers=2``) must all survive — no silent lost updates, valid
  JSON, no temp/corrupt litter.
- The lock must cover the whole transaction: a slow writer must not let
  a second worker overwrite its mutation.

Every test monkeypatches the module-level file constant so real user
state under ``data/`` / ``~/.nepse`` is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from src.alerts import history as alerts_history
from src.portfolio import holdings as portfolio_holdings
from src.utils import json_store
from src.watchlist import manager as watchlist_manager


# ───────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────


def _assert_valid_json(path: Path) -> object:
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert not list(path.parent.glob("*.tmp")), "atomic writes must not litter"
    return data


def _sleepy_save(monkeypatch, tmp_path: Path, delay: float = 0.05):
    """Inject a delay into the final atomic replace to widen the race.

    Deterministically reproduces the lost-update window: without a
    transaction lock, a slow writer lets a concurrent reader observe the
    pre-write state and then clobber it.  With ``update_json`` the delay
    happens *inside* the lock, so all mutations still survive.
    """
    real_replace = json_store.os.replace

    def slow_replace(src, dst):
        import time

        time.sleep(delay)
        return real_replace(src, dst)

    monkeypatch.setattr(json_store.os, "replace", slow_replace)


# ───────────────────────────────────────────────────────────────────
# Reproducer — deterministic lost update (pre-fix behaviour)
# ───────────────────────────────────────────────────────────────────


class TestLostUpdateReproducer:
    def test_read_modify_write_race_is_demonstrated(self, tmp_path, monkeypatch):
        """The classic lost update: two workers read v1, both write.

        This captures the *pre-fix* hazard in a deterministic form: A
        reads v1, B reads v1, A writes v2A, B writes v2B — A's update
        disappears while the file stays valid JSON.  The fix routes the
        store's mutations through ``update_json`` so this interleaving
        cannot occur.
        """
        target = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", target)

        # Simulate the sprint's Phase 2 interleaving at the store level.
        a = watchlist_manager.load_watchlist()   # A reads v1
        b = watchlist_manager.load_watchlist()   # B reads v1
        a["NABIL"] = {"enabled": True}           # A's update (v2A)
        b["APOLLO"] = {"enabled": True}          # B's update (v2B)
        # Sprint 12.1 (Phase 4): the deprecated raw ``save_watchlist``
        # writer was removed — the raw *blind* write primitive it
        # wrapped (``save_json``, no read-modify-write lock) is exactly
        # the hazard this test demonstrates, so it is exercised directly
        # to keep the pre-fix behaviour captured in a deterministic form.
        json_store.save_json(target, a, log_name="Watchlist")  # A writes
        json_store.save_json(target, b, log_name="Watchlist")  # B writes -> NABIL lost

        assert "NABIL" not in watchlist_manager.load_watchlist()
        assert "APOLLO" in watchlist_manager.load_watchlist()
        _assert_valid_json(target)


# ───────────────────────────────────────────────────────────────────
# json_store transactional helpers
# ───────────────────────────────────────────────────────────────────


class TestJsonStoreTransaction:
    def test_update_json_applies_mutator(self, tmp_path):
        target = tmp_path / "t.json"

        def mutator(state):
            state["added"] = True
            return state

        result = json_store.update_json(target, mutator, {})
        assert result == {"added": True}
        assert json.loads(target.read_text(encoding="utf-8")) == {"added": True}

    def test_update_json_returns_tuple_result(self, tmp_path):
        target = tmp_path / "t.json"

        def mutator(state):
            state["x"] = 1
            return state, "ok"

        assert json_store.update_json(target, mutator, {}) == "ok"
        assert json.loads(target.read_text(encoding="utf-8")) == {"x": 1}

    def test_update_json_serialises_slow_writers(self, tmp_path, monkeypatch):
        """A slow writer inside the lock must not lose another's update."""
        target = tmp_path / "t.json"
        _sleepy_save(monkeypatch, tmp_path)
        results: list[bool] = []
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()

            def mutator(state):
                state[f"K{i}"] = i
                return state

            results.append(json_store.update_json(target, mutator, {}) is not None)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        data = _assert_valid_json(target)
        assert all(f"K{i}" in data for i in range(8)), (
            f"lost updates: only {sorted(data)} present"
        )

    def test_update_json_mutator_exception_leaves_state_intact(self, tmp_path):
        target = tmp_path / "t.json"
        json_store.save_json(target, {"keep": True})

        def bad_mutator(state):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            json_store.update_json(target, bad_mutator, {})
        assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True}


# ───────────────────────────────────────────────────────────────────
# Watchlist — cross-worker add/remove survival
# ───────────────────────────────────────────────────────────────────


class TestWatchlistConcurrency:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.file)

    def test_concurrent_adds_all_survive(self, monkeypatch, tmp_path):
        _sleepy_save(monkeypatch, tmp_path)
        barrier = threading.Barrier(16)
        outcomes: list[bool] = []

        def worker(i: int) -> None:
            barrier.wait()
            outcomes.append(watchlist_manager.add_stock(f"SYM{i:02d}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        state = _assert_valid_json(self.file)
        assert all(f"SYM{i:02d}" in state for i in range(16)), (
            f"lost updates: only {sorted(state)} present"
        )
        assert all(outcomes), "every add must report success"

    def test_concurrent_add_remove_same_symbol(self):
        """Add/remove the same symbol concurrently must not corrupt."""
        barrier = threading.Barrier(12)

        def worker(i: int) -> None:
            barrier.wait()
            if i % 2 == 0:
                watchlist_manager.add_stock("NABIL")
            else:
                watchlist_manager.remove_stock("NABIL")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        # File must be valid regardless of the add/remove interleaving.
        _assert_valid_json(self.file)

    def test_duplicate_add_returns_false_under_contention(self):
        watchlist_manager.add_stock("NABIL")
        assert watchlist_manager.add_stock("nabil") is False
        assert watchlist_manager.remove_stock("missing") is False

    def test_remove_missing_is_noop(self):
        assert watchlist_manager.remove_stock("nope") is False
        assert watchlist_manager.load_watchlist() == {}


# ───────────────────────────────────────────────────────────────────
# Alert history — cross-worker survival
# ───────────────────────────────────────────────────────────────────


class TestAlertHistoryConcurrency:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "alerts" / "history.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", self.file)

    @staticmethod
    def _result(symbol: str, i: int) -> dict:
        return {
            "signal": "BUY",
            "confidence": 80 + i,
            "score": 5,
            "price": 100.0 + i,
            "trend": "UPTREND",
            "volume_signal": "NORMAL",
            "relative_volume": 1.0,
            "milestones": {"target1": False, "target2": False, "target3": False},
        }

    def test_concurrent_update_state_all_symbols_survive(self, monkeypatch, tmp_path):
        _sleepy_save(monkeypatch, tmp_path)
        barrier = threading.Barrier(16)
        errors: list[Exception] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                for _ in range(3):
                    alerts_history.update_state(
                        f"SYM{i:02d}", self._result(f"SYM{i:02d}", i)
                    )
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)

        assert errors == []
        state = _assert_valid_json(self.file)
        assert len(state) == 16, f"lost updates: only {sorted(state)} present"
        assert all(f"SYM{i:02d}" in state for i in range(16))

    def test_save_alerts_concurrent_symbols_survive(self, monkeypatch, tmp_path):
        _sleepy_save(monkeypatch, tmp_path)
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()
            alerts_history.save_alerts(
                f"SYM{i:02d}",
                [{"type": "INITIAL", "priority": 1, "message": f"msg{i}"}],
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        state = _assert_valid_json(self.file)
        assert all(f"SYM{i:02d}" in state for i in range(8))


# ───────────────────────────────────────────────────────────────────
# Portfolio — cross-worker survival
# ───────────────────────────────────────────────────────────────────


class TestPortfolioConcurrency:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "portfolio.json"
        monkeypatch.setattr(portfolio_holdings, "PORTFOLIO_FILE", self.file)

    def test_concurrent_position_updates_all_survive(self, monkeypatch, tmp_path):
        _sleepy_save(monkeypatch, tmp_path)
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()

            def mutator(holdings):
                holdings.append(
                    {
                        "symbol": f"SYM{i:02d}",
                        "quantity": 10 + i,
                        "average_price": 100.0,
                    }
                )
                return holdings

            portfolio_holdings.update_portfolio(mutator)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        state = _assert_valid_json(self.file)
        symbols = {h["symbol"] for h in state}
        assert all(f"SYM{i:02d}" in symbols for i in range(8)), (
            f"lost updates: only {sorted(symbols)} present"
        )


# ───────────────────────────────────────────────────────────────────
# Cross-process (uvicorn --workers=2 equivalent) survival
# ───────────────────────────────────────────────────────────────────


_CHILD_CODE = textwrap.dedent(
    """
    import os
    import sys
    from pathlib import Path

    target = Path(sys.argv[1])
    kind = sys.argv[2]
    start, count = int(sys.argv[3]), int(sys.argv[4])

    if kind == "watchlist":
        from src.watchlist import manager as m
        m.WATCHLIST_FILE = target
        for i in range(start, start + count):
            m.add_stock(f"SYM{i:04d}")
    elif kind == "alerts":
        from src.alerts import history as h
        h.HISTORY_FILE = target
        for i in range(start, start + count):
            h.update_state(
                f"SYM{i:04d}",
                {
                    "signal": "BUY", "confidence": 90, "score": 5,
                    "price": 100.0, "trend": "UPTREND",
                    "volume_signal": "NORMAL", "relative_volume": 1.0,
                    "milestones": {"target1": False, "target2": False,
                                   "target3": False},
                },
            )
    elif kind == "portfolio":
        from src.portfolio import holdings as p
        p.PORTFOLIO_FILE = target
        for i in range(start, start + count):
            p.update_portfolio(
                lambda hs, i=i: hs
                + [{"symbol": f"SYM{i:04d}", "quantity": 1,
                    "average_price": 100.0}]
            )
    print("done")
    """
)


def _run_child(target: Path, kind: str, start: int, count: int) -> None:
    proc = subprocess.run(
        [
            sys.executable, "-c", _CHILD_CODE,
            str(target), kind, str(start), str(count),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"child failed: {proc.stderr[-2000:]}"


@pytest.mark.parametrize("kind", ["watchlist", "alerts", "portfolio"])
class TestCrossProcessSurvival:
    def test_two_processes_no_lost_updates(self, tmp_path, monkeypatch, kind):
        target = tmp_path / f"state_{kind}.json"
        # Two OS processes mutate disjoint symbol ranges concurrently.
        # Without the transaction lock the second process's writes
        # clobber the first's (lost updates); with it, all survive.
        p1 = threading.Thread(target=_run_child, args=(target, kind, 0, 25))
        p2 = threading.Thread(target=_run_child, args=(target, kind, 25, 25))
        p1.start()
        p2.start()
        p1.join(timeout=300)
        p2.join(timeout=300)

        data = _assert_valid_json(target)
        if kind == "watchlist":
            assert all(f"SYM{i:04d}" in data for i in range(50)), (
                f"lost updates: only {len(data)} symbols"
            )
        elif kind == "alerts":
            assert len(data) == 50, f"lost updates: only {len(data)} symbols"
        else:
            symbols = {h["symbol"] for h in data}
            assert all(f"SYM{i:04d}" in symbols for i in range(50)), (
                f"lost updates: only {len(symbols)} holdings"
            )
