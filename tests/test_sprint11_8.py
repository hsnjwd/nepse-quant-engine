"""Sprint 11.8 — Watchlist ordering semantics, warm API gate & observability.

Covers:

- **Watchlist ordering semantics** (Phase 2/3): the production mutation
  path (``add_stock`` / ``remove_stock`` via ``update_json``) preserves
  *insertion order* — first add fixes the position, duplicate adds do
  not move it, removal frees the slot, re-adding appends at the end,
  and the ordering survives persistence/reload exactly.  Concurrent
  mutations must produce a deterministic final state consistent with
  transaction serialization (each thread's own relative order is
  preserved) with no duplicates, no lost successful updates, and no
  tmp/lock litter.
- **json_store lock observability** (Phase 5): lock-contention counters
  (retries / stale recoveries / timeouts) exposed via
  ``json_store.lock_stats()`` and the ``/metrics`` endpoint, plus the
  indicator-cache and process/worker identity fields.
- **Warm /api/analyze gate** (Phase 4): the ``benchmarks.ci_gate``
  warm-API ratio check exists, is wired into the gate conjunction, and
  PASSes on a working cache.

Every test monkeypatches the module-level file constant so real user
state under ``data/`` is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from src.utils import json_store
from src.watchlist import manager as watchlist_manager


# ───────────────────────────────────────────────────────────────────
# Watchlist ordering — sequential semantics (Phase 2)
# ───────────────────────────────────────────────────────────────────


class TestWatchlistOrdering:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.file)

    def _order(self) -> list[str]:
        return list(watchlist_manager.load_watchlist().keys())

    def test_add_a_b_c_keeps_insertion_order(self):
        for symbol in ("A", "B", "C"):
            assert watchlist_manager.add_stock(symbol) is True
        assert self._order() == ["A", "B", "C"]

    def test_duplicate_add_does_not_move_position(self):
        for symbol in ("A", "B", "C"):
            watchlist_manager.add_stock(symbol)
        assert watchlist_manager.add_stock("A") is False
        assert self._order() == ["A", "B", "C"]

    def test_remove_frees_slot(self):
        for symbol in ("A", "B", "C"):
            watchlist_manager.add_stock(symbol)
        assert watchlist_manager.remove_stock("B") is True
        assert self._order() == ["A", "C"]

    def test_readd_appends_at_end(self):
        for symbol in ("A", "B", "C"):
            watchlist_manager.add_stock(symbol)
        watchlist_manager.remove_stock("B")
        assert watchlist_manager.add_stock("B") is True
        assert self._order() == ["A", "C", "B"]

    def test_reload_preserves_exact_order(self):
        """Persistence/reload must preserve the exact ordering."""
        for symbol in ("A", "B", "C"):
            watchlist_manager.add_stock(symbol)
        watchlist_manager.remove_stock("B")
        watchlist_manager.add_stock("B")
        assert list(json.loads(self.file.read_text(encoding="utf-8")).keys()) == [
            "A", "C", "B",
        ]
        # Fresh load (as after a restart) sees the same order.
        assert self._order() == ["A", "C", "B"]

    def test_no_duplicate_symbols_ever(self):
        for symbol in ("A", "A", "A", "B", "C"):
            watchlist_manager.add_stock(symbol)
        data = json.loads(self.file.read_text(encoding="utf-8"))
        assert len(data) == len(set(data)) == 3

    def test_corruption_recovery_preserves_ordering_capability(self):
        """After corruption recovery the ordering semantics still hold."""
        self.file.write_text("{broken", encoding="utf-8")
        assert watchlist_manager.load_watchlist() == {}
        for symbol in ("A", "B"):
            watchlist_manager.add_stock(symbol)
        assert self._order() == ["A", "B"]


# ───────────────────────────────────────────────────────────────────
# Watchlist ordering — concurrent mutations (Phase 3)
# ───────────────────────────────────────────────────────────────────


class TestWatchlistConcurrentOrdering:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.file)

    def test_unique_adds_all_survive_no_duplicates(self):
        barrier = threading.Barrier(12)
        outcomes: list[bool] = []

        def worker(i: int) -> None:
            barrier.wait()
            outcomes.append(watchlist_manager.add_stock(f"SYM{i:02d}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert all(outcomes), "every successful add must report True"
        data = json.loads(self.file.read_text(encoding="utf-8"))
        assert len(data) == len(set(data)) == 12
        assert all(f"SYM{i:02d}" in data for i in range(12))

    def test_duplicate_adds_under_contention_never_duplicate(self):
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()
            for _ in range(5):
                watchlist_manager.add_stock("SHARED")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        data = json.loads(self.file.read_text(encoding="utf-8"))
        assert len(data) == len(set(data)) == 1

    def test_adds_and_removals_all_survive(self):
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()
            for j in range(4):
                watchlist_manager.add_stock(f"K{i:02d}_{j}")
                if j % 2 == 0:
                    watchlist_manager.remove_stock(f"K{i:02d}_{j}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        data = json.loads(self.file.read_text(encoding="utf-8"))
        # Even-indexed symbols were removed; odd-indexed must survive.
        assert all(f"K{i:02d}_{j}" in data for i in range(8) for j in (1, 3))
        assert not any(f"K{i:02d}_{j}" in data for i in range(8) for j in (0, 2))
        assert len(data) == len(set(data))

    def test_remove_and_readd_concurrent_deterministic(self):
        """Remove + re-add races must leave a valid single entry.

        The final state (present/absent) is serialized by the lock, but
        whatever the outcome the file must be valid with no duplicates
        and no litter.
        """
        barrier = threading.Barrier(10)

        def worker(i: int) -> None:
            barrier.wait()
            watchlist_manager.remove_stock("NABIL")
            watchlist_manager.add_stock("NABIL")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        data = json.loads(self.file.read_text(encoding="utf-8"))
        assert len(data) == len(set(data)) == 1
        assert not list(self.file.parent.glob("*.tmp"))
        assert not list(self.file.parent.glob("*.lock"))

    def test_thread_relative_order_preserved_under_contention(self):
        """Each thread's own add order is preserved in the final state.

        Because every mutation commits atomically under the transaction
        lock, a thread that adds X then Y must end up with X before Y in
        the persisted ordering — regardless of how other threads'
        commits interleave.
        """
        barrier = threading.Barrier(8)
        errors: list[Exception] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                watchlist_manager.add_stock(f"T{i:02d}_A")
                watchlist_manager.add_stock(f"T{i:02d}_B")
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert errors == []
        order = list(json.loads(self.file.read_text(encoding="utf-8")).keys())
        pos = {s: i for i, s in enumerate(order)}
        for i in range(8):
            assert pos[f"T{i:02d}_A"] < pos[f"T{i:02d}_B"], (
                f"thread {i} relative order lost in {order}"
            )


# ───────────────────────────────────────────────────────────────────
# Watchlist ordering — cross-process (uvicorn --workers=2 equivalent)
# ───────────────────────────────────────────────────────────────────


_CHILD_CODE = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from src.watchlist import manager as m

    m.WATCHLIST_FILE = Path(sys.argv[1])
    prefix = sys.argv[2]
    for i in range(3):
        m.add_stock(f"{prefix}{i}")
    print("done")
    """
)


def _run_ordering_child(target: Path, prefix: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_CODE, str(target), prefix],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"child failed: {proc.stderr[-2000:]}"


class TestWatchlistCrossProcessOrdering:
    def test_two_processes_preserve_each_process_relative_order(self, tmp_path, monkeypatch):
        target = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", target)

        p1 = threading.Thread(target=_run_ordering_child, args=(target, "P1"))
        p2 = threading.Thread(target=_run_ordering_child, args=(target, "P2"))
        p1.start()
        p2.start()
        p1.join(timeout=300)
        p2.join(timeout=300)

        data = json.loads(target.read_text(encoding="utf-8"))
        order = list(data.keys())
        assert len(data) == len(set(order)) == 6
        pos = {s: i for i, s in enumerate(order)}
        # Each process's own commit order must be preserved even though
        # the two processes interleave arbitrarily.
        for prefix in ("P1", "P2"):
            assert pos[f"{prefix}0"] < pos[f"{prefix}1"] < pos[f"{prefix}2"], order


# ───────────────────────────────────────────────────────────────────
# json_store lock observability counters (Phase 5)
# ───────────────────────────────────────────────────────────────────


class TestJsonStoreLockObservability:
    def test_lock_stats_shape(self):
        stats = json_store.lock_stats()
        assert set(stats) == {"retries", "stale_recoveries", "timeouts"}
        assert all(v >= 0 for v in stats.values())

    def test_stale_lock_break_increments_stale_recoveries(self, tmp_path, monkeypatch):
        target = tmp_path / "s.json"
        lock = json_store._lock_file_for(target)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("9999", encoding="utf-8")
        # Age the lock past the stale threshold so the next acquire
        # breaks it deterministically.
        old = time.time() - json_store._LOCK_STALE_S - 1.0
        os.utime(lock, (old, old))

        before = json_store.lock_stats()["stale_recoveries"]
        with json_store.locked_json(target):
            pass
        after = json_store.lock_stats()

        assert after["stale_recoveries"] == before + 1
        assert not lock.exists()
        # A *live* lock held concurrently would instead bump retries;
        # here we only assert the deterministic stale path.

    def test_contention_bumps_retries(self, tmp_path, monkeypatch):
        """A held (non-stale) lock bumps retries, not stale recoveries."""
        target = tmp_path / "r.json"
        lock = json_store._lock_file_for(target)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("9999", encoding="utf-8")
        os.utime(lock, (time.time(), time.time()))  # fresh -> not stale

        before = json_store.lock_stats()
        with pytest.raises(TimeoutError):
            # Tight deadline so the loop exits quickly after retrying.
            monkeypatch.setattr(json_store, "_LOCK_ACQUIRE_TIMEOUT_S", 0.2)
            monkeypatch.setattr(json_store, "_LOCK_RETRY_DELAY_S", 0.02)
            with json_store.locked_json(target):
                pass

        after = json_store.lock_stats()
        assert after["retries"] > before["retries"]
        assert after["stale_recoveries"] == before["stale_recoveries"]
        assert after["timeouts"] == before["timeouts"] + 1


# ───────────────────────────────────────────────────────────────────
# /metrics observability fields (Phase 5)
# ───────────────────────────────────────────────────────────────────


class TestMetricsObservability:
    def test_metrics_exposes_worker_cache_lock_fields(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        payload = resp.json()

        assert "process" in payload
        assert payload["process"]["pid"] == os.getpid()
        assert payload["process"]["hostname"]

        assert "indicator_cache" in payload
        ic = payload["indicator_cache"]
        for key in ("hits", "misses", "entries", "max_entries", "hit_rate", "enabled"):
            assert key in ic

        assert "json_store" in payload
        assert set(payload["json_store"]) == {"retries", "stale_recoveries", "timeouts"}

        assert "scanner_cache" in payload  # pre-existing contract intact


# ───────────────────────────────────────────────────────────────────
# Warm /api/analyze CI gate (Phase 4)
# ───────────────────────────────────────────────────────────────────


class TestWarmApiGate:
    def test_gate_runs_and_warm_api_check_passes(self, ci_gate_artifact):
        """The gate must run end-to-end with the warm-API check wired in.

        Sprint 12.0 Phase 11: the full gate is measured once per test
        session via the ``ci_gate_artifact`` session fixture, cutting the
        previous three full gate runs down to one without weakening any
        assertion (the measured ratios are machine-deterministic for the
        fixed synthetic corpus).
        """
        from benchmarks import ci_gate

        artifact = ci_gate_artifact

        assert "warm_api" in artifact["checks"]
        assert "api_analyze_warm_cold_ratio" in artifact
        assert 0.0 < artifact["api_analyze_warm_cold_ratio"] < 1.0
        assert artifact["warm_api_ratio_max"] == ci_gate.WARM_API_RATIO_MAX
        # On a working cache the machine-independent warm-API check must
        # pass (ratio well below 1.0), regardless of the absolute ms.
        # (Deliberately NOT asserting ``passed`` — the full verdict also
        # includes the machine-dependent absolute-baseline check, which
        # could fail on a slow CI runner.)
        assert artifact["checks"]["warm_api"] is True
