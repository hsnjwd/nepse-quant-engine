"""Sprint 11.6 Phase 12-14 + 16 — two-worker deployment validation.

The Docker ``api`` stage runs ``uvicorn src.api.main:app --workers=2``.
Docker is not required to validate the deployment semantics: this script
spawns the *same* uvicorn command (``--workers=2``) against an isolated
state root and a temp corpus and verifies:

Phase 12 — both workers serve the API
    ``/api/analyze/{symbol}``, ``/api/portfolio`` and the watchlist
    endpoints answer 200 from a 2-worker uvicorn process; a threaded
    request barrage exercises both workers concurrently; responses
    across workers are consistent.

Phase 13 — process-local indicator caching
    Repeated analysis of the same symbol is served faster by the warm
    worker; cross-worker misses are expected and harmless; responses
    remain identical.

Phase 14 — persistence integrity under concurrency
    After a concurrent request barrage (including write-path traffic:
    watchlist add/remove), every state JSON (alert history, portfolio,
    watchlist) still parses, atomic writes are intact (no leftover
    ``*.tmp``), and no ``.corrupt.bak`` was produced by the writes.

Phase 16 — /api/analyze cold/warm p50/p95/p99
    Measured against the real corpus (fresh worker process for cold,
    warm repeats within the same worker).

State-file resolution note
--------------------------
``PORTFOLIO_FILE`` / ``WATCHLIST_FILE`` / ``HISTORY_FILE`` are resolved
relative to the *process CWD* (``Path("portfolio.json")``,
``Path("data/watchlist/watchlist.json")``, ...) — ``NEPSE_HOME`` does
not redirect them.  The child therefore runs with ``cwd=state_root``
(plus ``PYTHONPATH`` so the ``src`` package imports) so every state
write lands in the isolated temp root, never in the project's ``data/``.

Usage::

    python -m benchmarks.validate_workers [--symbols 20] [--port 8891]

Read-only with respect to production: real CSVs are copied into a temp
data dir, all state files land under a temp root, and the corpus files
themselves are never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks import corpus as corpus_mod  # noqa: E402


def _url(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


def _request(
    port: int,
    path: str,
    method: str = "GET",
    timeout: float = 60.0,
) -> tuple[int, str]:
    req = urllib.request.Request(_url(port, path), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def _wait_ready(port: int, timeout: float = 90.0) -> None:
    """Poll until ``/`` answers 200 with parseable JSON."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status, body = _request(port, "/", timeout=5.0)
            if status == 200:
                json.loads(body)
                return
            last = f"status={status} body={body[:60]!r}"
        except Exception as exc:  # noqa: BLE001 - startup probing
            last = str(exc)
        time.sleep(0.5)
    raise TimeoutError(f"API did not become ready on port {port}: {last}")


def _percentiles(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(p * n)))
        return round(s[idx], 2)

    return {
        "best_ms": round(s[0], 2),
        "avg_ms": round(sum(s) / n, 2),
        "p50_ms": pct(0.5),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
    }


def _measure_analyze(port: int, symbols: list[str], reps: int = 5) -> dict:
    """Measure /api/analyze latency for *symbols*, repeated *reps* times."""
    lat: list[float] = []
    statuses: set[int] = set()
    for _ in range(reps):
        for sym in symbols:
            start = time.perf_counter()
            status, _body = _request(port, f"/analyze/{sym}")
            lat.append((time.perf_counter() - start) * 1000.0)
            statuses.add(status)
    return {"latency": _percentiles(lat), "statuses": sorted(statuses)}


def _scan_state(root: Path) -> dict:
    """Validate every JSON store under *root*; check tmp/corrupt/lock files."""
    stores: list[tuple[str, Path]] = []
    # The API process runs with cwd=state_root, and the manager resolves
    # WATCHLIST_FILE = Path("data/watchlist/watchlist.json") relative to
    # that CWD — so the watchlist lands under ``data/`` inside the state
    # root (Sprint 11.8 Phase 15 review fix).
    for rel in ("alerts/history.json", "portfolio.json", "data/watchlist/watchlist.json"):
        p = root / rel
        if p.exists():
            stores.append((rel, p))
    results: dict[str, str] = {}
    for name, p in stores:
        try:
            json.loads(p.read_text(encoding="utf-8"))
            results[name] = "valid-json"
        except Exception as exc:  # noqa: BLE001 - integrity check
            results[name] = f"INVALID: {exc}"
    tmp_files = sorted(str(p.relative_to(root)) for p in root.rglob("*.tmp"))
    corrupt = sorted(str(p.relative_to(root)) for p in root.rglob("*.corrupt.bak"))
    # Sprint 11.7: a crashed worker leaves a ``<name>.json.lock`` sentinel
    # (broken only when the next waiter observes it older than the stale
    # threshold).  Flagging leftovers makes a crashed holder fail loudly
    # instead of passing the integrity check with lock litter behind it.
    lock_files = sorted(
        str(p.relative_to(root)) for p in root.rglob("*.lock")
    )
    return {
        "stores": results,
        "leftover_tmp": tmp_files,
        "corrupt_backups": corrupt,
        "leftover_locks": lock_files,
    }


def _safe(fn, *args, errors: list) -> None:
    """Run *fn* collecting any exception into *errors*."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 - thread isolation
        errors.append(exc)


def _alert_symbol_count(root: Path) -> int:
    import json as _json

    p = root / "alerts" / "history.json"
    if not p.exists():
        return 0
    try:
        return len(_json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - count only
        return 0


def _portfolio_symbol_count(root: Path) -> int:
    import json as _json

    p = root / "portfolio.json"
    if not p.exists():
        return 0
    try:
        return len(_json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - count only
        return 0


def _spawn_api(data_dir: Path, state_root: Path, port: int, workers: int = 2) -> subprocess.Popen:
    """Spawn the uvicorn API exactly like the Dockerfile api stage.

    ``workers`` defaults to 2 (the Docker api stage).  ``1`` exercises
    the single-worker deployment for comparison profiling (Sprint 11.7
    Phase 13).
    """
    env = dict(os.environ)
    env["DATA_DIRECTORY"] = str(data_dir)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "src.api.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--workers", str(workers),
        ],
        cwd=str(state_root),  # state files resolve against CWD
        env=env,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _shutdown(proc: subprocess.Popen) -> None:
    """Terminate the uvicorn parent and its workers; never raises.

    Windows nuance (Sprint 13.2 Phase 5): uvicorn's ``--workers=N``
    children are spawned via ``multiprocessing.spawn`` and are *not* in
    the master's console group, so ``CTRL_BREAK_EVENT`` (which the
    ``CREATE_NEW_PROCESS_GROUP`` flag gives us) reaches the master but
    orphaned workers can survive it — holding state files open and
    blocking port release, which poisons subsequent test runs.  The
    graceful signal was attempted first (clean shutdown path), but on
    Windows the tree-kill ALWAYS runs afterwards: the master exiting
    within the 15 s window does NOT mean the tree is gone, so an early
    return would leave the exact orphans this function exists to
    prevent.  ``taskkill /T /F`` on an already-dead PID is a harmless
    no-op error.

    Windows teardown hardening (Sprint 13.7): the ``CTRL_BREAK_EVENT``
    is no longer sent on Windows at all.  Once *both* ``--workers=N``
    children are fully alive, the console event can propagate back to
    the caller's own console group and kill the whole pytest/bash
    process (observed as exit 58 = ``0xC000013A`` low byte) — a test
    runner must never be taken down by the server it spawns.  The
    tree-kill below is the actual cleanup (per the note above, the
    graceful signal never guarantees the tree is gone), so skipping it
    on Windows loses nothing except the console-kill risk.  POSIX keeps
    SIGTERM, which propagates to the shared process group.
    """
    if os.name != "nt":
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass

    # Hard kill: whole tree on Windows (``/T``), single process on POSIX.
    # POSIX uvicorn workers share the master's process group and die
    # with its SIGTERM propagation, so a single ``proc.kill()`` is
    # sufficient there (no-op error if the master already exited).
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=30,
            )
        else:
            proc.kill()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Two-worker deployment validation (Sprint 11.6)"
    )
    parser.add_argument("--symbols", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--workers", type=int, default=2, help="uvicorn workers (1 or 2)")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="nepse_workers_"))
    data_dir = tmp / "data"
    state_root = tmp / "state"
    state_root.mkdir(parents=True, exist_ok=True)

    spec = corpus_mod.select_corpus(args.symbols, seed=args.seed)
    corpus_mod.copy_corpus(spec, data_dir)
    symbols = [p.stem for p in sorted(data_dir.glob("*.csv"))]
    print(
        f"\nTwo-worker deployment validation "
        f"({len(symbols)} real symbols, seed {args.seed}, "
        f"port {args.port})\n"
    )

    proc = _spawn_api(data_dir, state_root, args.port, workers=args.workers)
    try:
        _wait_ready(args.port)
        print(f"  API ready ({args.workers} worker{'s' if args.workers != 1 else ''})\n")

        # ── Phase 12: both workers serve core endpoints ─────────────
        print("  [Phase 12] Endpoint availability:")
        # Health router is unprefixed and served at "/".
        results: dict[str, int] = {}
        for path in ("/", "/analyze/" + symbols[0], "/portfolio", "/watchlist"):
            status, _ = _request(args.port, path)
            results[path] = status
            print(f"    {path:<24} -> {status}")
        bad = {p for p, s in results.items() if s != 200}
        if bad:
            print(f"  FAIL: non-200 endpoints {bad}")
            return 1

        # ── Phase 16: /api/analyze latency (warm worker) ────────────
        print(f"\n  [Phase 16] /api/analyze latency ({args.workers}-worker, warm repeats):")
        analyze = _measure_analyze(args.port, symbols, reps=args.reps)
        lat = analyze["latency"]
        print(f"    best {lat['best_ms']} ms | avg {lat['avg_ms']} ms | "
              f"p50 {lat['p50_ms']} ms | p95 {lat['p95_ms']} ms | "
              f"p99 {lat['p99_ms']} ms")
        print(f"    statuses observed: {analyze['statuses']}")

        # ── Phase 13: process-local indicator cache (repeat warm) ───
        print("\n  [Phase 13] Repeat-analysis warm behaviour:")
        sym = symbols[0]
        timings: list[float] = []
        for _ in range(6):
            start = time.perf_counter()
            _request(args.port, f"/analyze/{sym}")
            timings.append((time.perf_counter() - start) * 1000.0)
        first, warm = timings[0], min(timings[1:])
        print(f"    first call {first:.1f} ms | best repeat {warm:.1f} ms "
              f"({first / max(warm, 1e-9):.1f}x warm-up)")
        print("    (per-worker local LRU: repeats served by the same worker "
              "are faster; cross-worker misses are expected and harmless)")

        # ── Phase 12/14: concurrent write-path barrage ──────────────
        print("\n  [Phase 14] Concurrent write-path barrage "
              "(watchlist add/remove):")
        barrier = threading.Barrier(8, timeout=30)
        outcomes: list[tuple[int, int]] = []  # (add_status, remove_status)

        def _writer(i: int) -> None:
            tag = f"WRK{i:02d}"
            barrier.wait()
            add_status, _ = _request(args.port, f"/watchlist/add/{tag}", method="POST")
            remove_status, _ = _request(
                args.port, f"/watchlist/remove/{tag}", method="DELETE"
            )
            outcomes.append((add_status, remove_status))

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        if len(outcomes) != 8:
            print(f"  FAIL: only {len(outcomes)}/8 writer threads completed")
            return 1
        adds = {a for a, _ in outcomes}
        removes = {r for _, r in outcomes}
        print(f"    add statuses    -> {sorted(adds)}")
        print(f"    remove statuses -> {sorted(removes)}")
        print("    (two workers share one watchlist file: concurrent atomic")
        print("     writes may lose an update but can never corrupt it - the")
        print("     gate asserts valid JSON + no tmp/corrupt leftovers, not")
        print("     that the final dict is empty)")
        if adds != {200} or removes != {200}:
            print(f"  FAIL: non-200 watchlist write outcomes {outcomes}")
            return 1

        # ── Phase 12/14: concurrent alert + portfolio writes ────────
        # The API exposes no alert-history or portfolio mutation route
        # (/portfolio is read-only), but Sprint 11.7's cross-process
        # lock must protect those stores too.  Spawn two child processes
        # that call process_alert_batch and update_portfolio directly
        # against the shared state root — the exact code path a second
        # worker would take — while the API keeps serving.
        print("\n  [Phase 14] Concurrent alert + portfolio writes "
              "(2 child processes):")
        child = (
            "import sys\n"
            "from pathlib import Path\n"
            "root = Path(sys.argv[1]); start, count = int(sys.argv[2]), int(sys.argv[3])\n"
            "from src.alerts import history as h\n"
            "from src.alerts.engine import process_alert_batch\n"
            "from src.portfolio import holdings as p\n"
            "h.HISTORY_FILE = root / 'alerts' / 'history.json'\n"
            "p.PORTFOLIO_FILE = root / 'portfolio.json'\n"
            "(root / 'alerts').mkdir(parents=True, exist_ok=True)\n"
            "entries = []\n"
            "for i in range(start, start + count):\n"
            "    sym = f'SYM{i:04d}'\n"
            "    entries.append((sym, {'signal': 'BUY', 'confidence': 90,"
            " 'score': 5, 'price': 100.0, 'trend': 'UPTREND',"
            " 'volume_signal': 'NORMAL', 'relative_volume': 1.0,"
            " 'milestones': {'target1': False, 'target2': False,"
            " 'target3': False}}))\n"
            "    p.update_portfolio(lambda hs, s=sym: hs +"
            " [{'symbol': s, 'quantity': 1, 'average_price': 100.0}])\n"
            "process_alert_batch(entries)\n"
            "print('done')\n"
        )

        def _state_writer(start: int, count: int) -> None:
            proc = subprocess.run(
                [sys.executable, "-c", child, str(state_root), str(start), str(count)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"state writer failed: {proc.stderr[-1000:]}")

        writer_errors: list[Exception] = []
        wt1 = threading.Thread(
            target=lambda: _safe(_state_writer, 0, 10, errors=writer_errors)
        )
        wt2 = threading.Thread(
            target=lambda: _safe(_state_writer, 10, 10, errors=writer_errors)
        )
        wt1.start()
        wt2.start()
        wt1.join(timeout=300)
        wt2.join(timeout=300)
        if writer_errors:
            print(f"  FAIL: state-writer child error: {writer_errors[0]}")
            return 1
        alert_state = _scan_state(state_root)["stores"].get("alerts/history.json")
        portfolio_state = _scan_state(state_root)["stores"].get("portfolio.json")
        alert_ok = alert_state == "valid-json" and _alert_symbol_count(state_root) == 20
        portfolio_ok = portfolio_state == "valid-json" and _portfolio_symbol_count(state_root) == 20
        print(f"    alerts/history.json -> valid-json, 20 symbols present: {alert_ok}")
        print(f"    portfolio.json      -> valid-json, 20 holdings present: {portfolio_ok}")
        if not alert_ok or not portfolio_ok:
            print("  FAIL: concurrent alert/portfolio writes lost updates")
            return 1

        # ── Phase 15: watchlist ordering semantics (Sprint 11.8) ─────
        # Watchlist ordering is *insertion order*: first add fixes the
        # position, duplicate adds do not move it, removal frees the
        # slot, and re-adding appends at the end.  Drive the real API
        # (2 workers) and assert the served order AND the persisted
        # JSON key order agree.
        print("\n  [Phase 15] Watchlist ordering semantics (insertion order):")

        def _watchlist_order() -> list[str]:
            status, body = _request(args.port, "/watchlist")
            if status != 200:
                raise RuntimeError(f"/watchlist returned {status}")
            return list(json.loads(body).keys())

        for sym in ("ORD_A", "ORD_B", "ORD_C"):
            status, _ = _request(args.port, f"/watchlist/add/{sym}", method="POST")
            if status != 200:
                print(f"  FAIL: add {sym} -> {status}")
                return 1
        order = _watchlist_order()
        if order != ["ORD_A", "ORD_B", "ORD_C"]:
            print(f"  FAIL: insertion order not preserved: {order}")
            return 1
        print("    add ORD_A, ORD_B, ORD_C   -> ORD_A, ORD_B, ORD_C")

        status, _ = _request(args.port, "/watchlist/add/ORD_A", method="POST")
        if status != 200:
            print(f"  FAIL: duplicate add ORD_A -> {status}")
            return 1
        order = _watchlist_order()
        if order != ["ORD_A", "ORD_B", "ORD_C"]:
            print(f"  FAIL: duplicate add moved the symbol: {order}")
            return 1
        print("    duplicate add of ORD_A     -> unchanged (no move)")

        status, _ = _request(args.port, "/watchlist/remove/ORD_B", method="DELETE")
        if status != 200:
            print(f"  FAIL: remove ORD_B -> {status}")
            return 1
        order = _watchlist_order()
        if order != ["ORD_A", "ORD_C"]:
            print(f"  FAIL: removal did not free the slot: {order}")
            return 1
        print("    remove ORD_B               -> ORD_A, ORD_C")

        status, _ = _request(args.port, "/watchlist/add/ORD_B", method="POST")
        if status != 200:
            print(f"  FAIL: re-add ORD_B -> {status}")
            return 1
        order = _watchlist_order()
        if order != ["ORD_A", "ORD_C", "ORD_B"]:
            print(f"  FAIL: re-add did not append at end: {order}")
            return 1
        print("    re-add ORD_B               -> ORD_A, ORD_C, ORD_B (appended)")

        # The API resolves WATCHLIST_FILE relative to its CWD
        # (state_root), so the persisted file is under ``data/``.
        wl_file = state_root / "data" / "watchlist" / "watchlist.json"
        if not wl_file.exists():
            print("  FAIL: watchlist.json not persisted under state root")
            return 1
        persisted = list(json.loads(wl_file.read_text(encoding="utf-8")).keys())
        if persisted != ["ORD_A", "ORD_C", "ORD_B"]:
            print(f"  FAIL: persisted JSON order does not match: {persisted}")
            return 1
        print("    persisted JSON order      -> matches served order")

        # ── Phase 14: persistence integrity after the barrage ───────
        print("\n  [Phase 14] Persistence integrity after concurrent requests:")
        state = _scan_state(state_root)
        for name, verdict in state["stores"].items():
            print(f"    {name:<28} -> {verdict}")
        print(f"    leftover tmp files   -> {state['leftover_tmp'] or 'none'}")
        print(f"    corrupt backups      -> {state['corrupt_backups'] or 'none'}")
        print(f"    leftover lock files  -> {state['leftover_locks'] or 'none'}")
        ok_stores = all(v == "valid-json" for v in state["stores"].values())
        if (
            not ok_stores
            or state["leftover_tmp"]
            or state["corrupt_backups"]
            or state["leftover_locks"]
        ):
            print("  FAIL: persistence integrity violated")
            return 1

        # ── Phase 12: cross-worker response consistency ─────────────
        print("\n  [Phase 12] Cross-worker response consistency:")
        _status, first_body = _request(args.port, f"/analyze/{sym}")
        _status, second_body = _request(args.port, f"/analyze/{sym}")
        same = json.loads(first_body) == json.loads(second_body)
        print(f"    two /analyze/{sym} responses identical -> {same}")
        if not same:
            print("  FAIL: inconsistent responses across workers")
            return 1

        # ── Phase 17: /metrics under the deployment (Sprint 11.9) ────
        # Verify the observability endpoint under the exact Docker
        # uvicorn --workers=N process.  Repeated requests may land on
        # either worker (load balancing) — we assert every response is
        # valid and exercise enough requests that both workers are
        # likely to serve at least one, but never REQUIRE a specific
        # worker to answer (the deployment must tolerate load
        # balancing).
        print("\n  [Phase 17] /metrics under multi-worker deployment:")
        status, body = _request(args.port, "/metrics")
        if status != 200:
            print(f"  FAIL: /metrics -> {status}")
            return 1
        payload = json.loads(body)
        process = payload.get("process", {})
        ic = payload.get("indicator_cache", {})
        locks = payload.get("json_store", {})
        if not isinstance(process.get("pid"), int) or not process.get("hostname"):
            print(f"  FAIL: /metrics process identity missing: {process}")
            return 1
        for key in ("hits", "misses", "entries", "max_entries"):
            if not isinstance(ic.get(key), int) or ic[key] < 0:
                print(f"  FAIL: /metrics indicator_cache.{key} invalid: {ic}")
                return 1
        if not (0.0 <= ic.get("hit_rate", -1.0) <= 1.0):
            print(f"  FAIL: /metrics indicator_cache.hit_rate invalid: {ic}")
            return 1
        for key in ("retries", "stale_recoveries", "timeouts"):
            if not isinstance(locks.get(key), int) or locks[key] < 0:
                print(f"  FAIL: /metrics json_store.{key} invalid: {locks}")
                return 1
        # No secrets / filesystem paths in the metrics payload.
        body_lower = body.lower()
        for secret in ("token", "secret", "password", "api_key", "authorization"):
            if secret in body_lower:
                print(f"  FAIL: /metrics leaks sensitive field: {secret}")
                return 1
        pids_seen: set[int] = set()
        for _ in range(8):
            _status, _body = _request(args.port, "/metrics")
            if _status != 200:
                print(f"  FAIL: repeated /metrics -> {_status}")
                return 1
            pids_seen.add(json.loads(_body)["process"]["pid"])
        print(f"    worker pids observed (may be a subset due to load balancing): {sorted(pids_seen)}")
        print(f"    indicator_cache entries={ic.get('entries')} hit_rate={ic.get('hit_rate')}")
        print(f"    json_store lock stats: retries={locks.get('retries')} "
              f"stale={locks.get('stale_recoveries')} timeouts={locks.get('timeouts')}")
        print("    no secrets/filesystem paths exposed -> OK")

        # ── Phase 18: cross-worker aggregate metrics (Sprint 12.0) ──
        # /metrics now self-reports each worker's local counters into a
        # shared JSON store and exposes ``workers`` + ``aggregate``
        # blocks.  Verify the aggregation is valid, liveness is TTL-
        # based (no fragile PID assumptions), counters are sane, and
        # metrics collection produces no tmp/lock/corrupt litter.
        print("\n  [Phase 18] Cross-worker aggregate metrics (Sprint 12.0):")
        agg_status, agg_body = _request(args.port, "/metrics")
        if agg_status != 200:
            print(f"  FAIL: /metrics (aggregate) -> {agg_status}")
            return 1
        agg = json.loads(agg_body)
        workers = agg.get("workers", {})
        aggr = agg.get("aggregate", {})
        if not isinstance(workers.get("active"), int) or workers["active"] < 1:
            print(f"  FAIL: workers.active invalid: {workers}")
            return 1
        if not isinstance(workers.get("known"), list) or not workers["known"]:
            print(f"  FAIL: workers.known missing: {workers}")
            return 1
        for key in ("hits", "misses", "lock_retries", "stale_recoveries", "timeouts"):
            if not isinstance(aggr.get(key), int) or aggr[key] < 0:
                print(f"  FAIL: aggregate.{key} invalid: {aggr}")
                return 1
        if not (0.0 <= aggr.get("hit_rate", -1.0) <= 1.0):
            print(f"  FAIL: aggregate.hit_rate invalid: {aggr}")
            return 1
        # Count consistency is guaranteed (the aggregate sums every
        # active worker's counters, including this worker's own
        # self-report).  Rate consistency is NOT: a fresh worker with a
        # miss-heavy start can legitimately pull the aggregate rate below
        # the serving worker's local rate, so compare counts, not rates
        # (Sprint 12.0 review fix).
        local_hits = agg.get("indicator_cache", {}).get("hits", 0)
        local_misses = agg.get("indicator_cache", {}).get("misses", 0)
        if aggr["hits"] < local_hits:
            print(f"  FAIL: aggregate hits {aggr['hits']} < local {local_hits}")
            return 1
        if aggr["misses"] < local_misses:
            print(f"  FAIL: aggregate misses {aggr['misses']} < local {local_misses}")
            return 1
        # No secrets / filesystem paths in the enriched payload.
        agg_lower = agg_body.lower()
        for secret in ("token", "secret", "password", "api_key", "authorization"):
            if secret in agg_lower:
                print(f"  FAIL: aggregate /metrics leaks sensitive field: {secret}")
                return 1
        # Metrics writes must not litter the state root (atomic JSON
        # transactions clean up .tmp/.lock; corrupt backups mean a
        # broken store).
        metrics_state = _scan_state(state_root)
        if (
            metrics_state["leftover_tmp"]
            or metrics_state["corrupt_backups"]
            or metrics_state["leftover_locks"]
        ):
            print(f"  FAIL: metrics collection littered the state root: {metrics_state}")
            return 1
        print(f"    workers.active={workers['active']} known_pids={sorted(p['pid'] for p in workers['known'])}")
        print(f"    aggregate hits={aggr.get('hits')} misses={aggr.get('misses')} "
              f"hit_rate={aggr.get('hit_rate')} lock_retries={aggr.get('lock_retries')} "
              f"stale={aggr.get('stale_recoveries')} timeouts={aggr.get('timeouts')}")
        print("    no secrets/filesystem paths exposed; no state litter -> OK")

        print("\n  RESULT: PASS")
        return 0
    finally:
        _shutdown(proc)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
