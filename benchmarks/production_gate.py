"""Sprint 13.0 — NEPSE Quant Engine production readiness gate.

A single consolidated command that determines whether the *entire*
engine works reliably as one integrated production system.  It does not
re-run every unit test — it validates the real integrated application:

    data -> DataService -> scanner -> indicators -> analyzer ->
    signals -> confidence -> ranking -> alerts -> FastAPI -> Streamlit

Sections
--------

1.  CORE PIPELINE  — in-process scan_market / analyze_stock / rank_market
    / process_alert_batch over a deterministic synthetic corpus plus a
    subset of the real shipped corpus (``data/raw``), state-isolated.
2.  API            — a real uvicorn HTTP server (single worker) serving
    the production ``src.api.main:app``; every critical endpoint is
    exercised over HTTP with cold/warm/repeated and invalid inputs.
3.  CACHE          — cold vs warm equivalence on stable analysis fields;
    scanner/indicator cache hit counters observed via ``/metrics``.
4.  PERSISTENCE    — concurrent writes, atomicity, corruption recovery,
    temp/lock cleanup on a private state root.
5.  MULTI-WORKER   — ``uvicorn --workers=2`` (the Docker api stage) with
    a concurrent request barrage; ``/metrics`` aggregation sanity
    (aggregate >= local where sum semantics apply) and state integrity.
6.  CONCURRENCY    — 5 and 10 concurrent requests across endpoints;
    success rate / failures / latency percentiles.
7.  STREAMLIT      — the real Streamlit app boots headless; its health
    endpoint answers; the Metrics page's ``fetch_metrics`` HTTP path
    consumes the running backend.
8.  PERFORMANCE    — the existing ``benchmarks.ci_gate`` regression gate
    plus the scanner warm-path baseline and the Sprint 12.3 cache-
    capacity guarantee (default capacity >= shipped corpus).
9.  RECOVERY       — API restart, corrupt-optional-data recovery,
    per-symbol failure isolation (bad CSV never breaks the scan).
10. SECURITY       — static audit of ``src`` (no unsafe deserialisation,
    no eval/exec, no hardcoded secrets) and a live ``/metrics`` leak
    check (no filesystem paths in the payload).
11. STARTUP/DEPLOY — clean uvicorn + Streamlit startup, and
    ``docker compose config`` validation when Docker is available.
12. BOT            — the real ``src.bot.telegram_bot`` module boots in a
    fresh subprocess against this section's live API: the real
    ``Application`` is built with the injected ``TELEGRAM_TOKEN`` (or a
    dummy when unset), every command handler is invoked through a
    minimal recording Update/Context double, and each handler's *real*
    HTTP call to the live server is verified (``/start`` help text,
    ``/analyze``, ``/backtest``, ``/signals``, ``/top10``, ``/buylist``,
    ``/selllist``, ``/strongbuy``, ``/market``, ``/watchlist``,
    ``/watchlist scan``, ``/portfolio``).  Live Telegram polling is not
    exercised — it needs a real token and network (documented, never
    hidden).

Statuses
--------

Every section reports exactly one of ``PASS`` / ``FAIL`` / ``SKIPPED``
/ ``CONDITIONAL`` — a section is never marked PASS without having been
executed.  The final verdict:

* ``NOT PRODUCTION READY``  — any FAIL (P0/P1-class finding).
* ``CONDITIONALLY PRODUCTION READY`` — no FAIL, at least one CONDITIONAL.
* ``PRODUCTION READY`` — no FAIL/CONDITIONAL; SKIPPED sections are
  listed and explained in the report.

Execution order
---------------

Sections run in this order: CORE PIPELINE, PERSISTENCE, PERFORMANCE,
then the API/CACHE/MULTI-WORKER/CONCURRENCY/STREAMLIT/RECOVERY/
SECURITY/STARTUP sections.  PERFORMANCE deliberately runs *before* any
uvicorn server is spawned: its ``ci_gate`` warm p99 tripwire is a
worst-of-60 per-symbol sample, and co-resident gate servers inflated it
past the 2000 ms threshold (>2000 ms vs ~96 ms standalone) — a quiet
machine is the honest baseline.  Do not move it later.

Isolation guarantees
--------------------

* Every server runs with ``cwd`` = a private state root and
  ``DATA_DIRECTORY`` pointed at a private corpus copy, exactly like
  ``benchmarks.validate_workers`` — production ``data/alerts``,
  ``portfolio.json``, ``data/watchlist`` and ``data/state`` are never
  read or written.
* In-process sections reuse ``benchmarks.pipeline._state_files``.
* The gate snapshots the four production state files (filename, size,
  st_mtime_ns) before and after and FAILS the run if they changed.

Usage::

    python -m benchmarks.production_gate [--synthetic 20] [--real 20]
        [--stability-min 0] [--out benchmarks/results/production_gate.json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Production state files the gate must never touch ──────────────
PROTECTED_STATE = [
    Path("data/alerts/history.json"),
    Path("portfolio.json"),
    Path("data/watchlist/watchlist.json"),
    Path("data/state/worker_metrics.json"),
]

# Stable analysis fields for cold/warm equivalence (alert state is
# intentionally excluded: the first analysis of a symbol emits INITIAL
# alerts, a repeat emits none — that is documented, stateful behaviour,
# not a warm-path divergence).
STABLE_FIELDS = [
    "price", "score", "confidence", "rsi", "macd", "atr", "trend",
    "support", "resistance", "pattern", "pattern_type", "volume_signal",
    "relative_volume", "volume_score", "signal",
]

PASS, FAIL, SKIPPED, CONDITIONAL = "PASS", "FAIL", "SKIPPED", "CONDITIONAL"

STATUS_LABEL = {
    PASS: "PASS",
    FAIL: "FAIL",
    SKIPPED: "SKIPPED",
    CONDITIONAL: "CONDITIONAL",
}


class Section:
    """One gate section with a status and supporting evidence."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.status: str = SKIPPED
        self.details: dict[str, Any] = {}
        self.notes: list[str] = []

    def pass_(self, **details: Any) -> "Section":
        self.status = PASS
        # Merge, never replace: sections accumulate measurements into
        # ``self.details`` before calling ``pass_()`` — overwriting would
        # wipe every piece of evidence from the artifact (reviewer-
        # confirmed evidence-loss bug: ``core_pipeline PASS {}``).
        self.details = {**self.details, **details}
        return self

    def fail(self, reason: str, **details: Any) -> "Section":
        self.status = FAIL
        self.details = {"reason": reason, **details}
        return self

    def skip(self, reason: str) -> "Section":
        self.status = SKIPPED
        self.details = {"reason": reason}
        return self

    def conditional(self, reason: str, **details: Any) -> "Section":
        self.status = CONDITIONAL
        # Merge (same evidence-preservation principle as ``pass_``): a
        # conditional section keeps any measurements accumulated before
        # the call instead of wiping them.
        self.details = {"reason": reason, **self.details, **details}
        return self

    def note(self, text: str) -> "Section":
        self.notes.append(text)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "details": self.details,
            "notes": self.notes,
        }


def _fmt_ms(seconds: float) -> float:
    return round(seconds * 1000.0, 2)


def _percentiles(values: list[float]) -> dict[str, float]:
    """p50/p95/p99 (+ best/avg) from a latency sample (ms)."""
    if not values:
        return {"avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(p * n)))
        return round(s[idx], 2)

    return {
        "avg_ms": round(sum(s) / n, 2),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
    }


# ───────────────────────────────────────────────────────────────────
# Shared HTTP helpers (mirror benchmarks.validate_workers)
# ───────────────────────────────────────────────────────────────────

def _url(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


def _request(
    port: int,
    path: str,
    method: str = "GET",
    timeout: float = 60.0,
) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(_url(port, path), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback test server
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _wait_ready(port: int, timeout: float = 90.0) -> str:
    """Poll until ``/`` answers 200 with parseable JSON; return last error."""
    deadline = time.monotonic() + timeout
    last = "not attempted"
    while time.monotonic() < deadline:
        try:
            status, body = _request(port, "/", timeout=5.0)
            if status == 200:
                json.loads(body)
                return ""
            last = f"status={status} body={body[:80]!r}"
        except Exception as exc:  # noqa: BLE001 - startup probing
            last = str(exc)
        time.sleep(0.5)
    return last


def _free_port(port: int) -> bool:
    """Best-effort check that *port* is not already bound."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_ports(count: int, start: int = 8911, end: int = 8999) -> list[int]:
    """Probe and return *count* free loopback ports.

    Gate sections must never collide with each other or with leftover
    servers from earlier runs (e.g. a killed process whose socket is
    still in TIME_WAIT on Windows).  Hard-coded ports made the gate
    fragile; dynamic allocation removes the collision class entirely.
    """
    import socket  # noqa: PLC0415

    chosen: list[int] = []
    for port in range(start, end):
        if len(chosen) >= count:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                chosen.append(port)
            except OSError:
                continue
    if len(chosen) < count:
        raise RuntimeError(
            f"could not allocate {count} free ports in [{start},{end})"
        )
    return chosen


def _offline_provider_env() -> dict[str, str]:
    """Env overrides that make every NEPSE provider URL fail fast.

    ``app.py`` calls ``DataService().start_background_refresh()`` at
    module level, so ``import app`` (the Streamlit section and the
    startup_deploy import smoke test) makes *real* HTTP calls to the
    live NEPSE providers unless the URLs point somewhere dead.  A dead
    loopback port fails with an instant connection-refused error and the
    engine's CSV fallback serves the private corpus — the same degraded
    mode the production engine already handles.  Without this the gate
    hangs on live-network timeouts and is non-deterministic.
    """
    return {
        "NEPSE_SCRAPER_URL": "http://127.0.0.1:1",
        "NEPSE_CLIENT_URL": "http://127.0.0.1:1",
        "GITHUB_DATASETS_URL": "http://127.0.0.1:1",
        "NEPSE_DATA_API_URL": "",
        "NEPALSTOCK_OFFICIAL_URL": "",
    }


# ───────────────────────────────────────────────────────────────────
# State snapshot + isolation
# ───────────────────────────────────────────────────────────────────

def snapshot_state(root: Path = PROJECT_ROOT) -> dict[str, dict[str, int]]:
    """Capture (size, st_mtime_ns) of every protected production state file."""
    snap: dict[str, dict[str, int]] = {}
    for rel in PROTECTED_STATE:
        p = root / rel
        try:
            st = p.stat()
            snap[str(rel)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
        except OSError:
            snap[str(rel)] = {"size": -1, "mtime_ns": -1}
    return snap


def verify_state_unchanged(before: dict, after: dict) -> bool:
    return before == after


def _state_notes(before: dict, after: dict) -> list[str]:
    changed = [k for k in before if before[k] != after.get(k)]
    return [] if not changed else [f"state file changed: {k}" for k in changed]


# ───────────────────────────────────────────────────────────────────
# Server lifecycle (shared by API / multi-worker / concurrency /
# streamlit-API / recovery sections)
# ───────────────────────────────────────────────────────────────────

class ServerPool:
    """Spawn and track uvicorn servers on private state/corpus roots.

    Each server runs the exact production entrypoint
    (``uvicorn src.api.main:app --workers=N``) with ``cwd`` = a private
    state root and ``DATA_DIRECTORY`` = a private corpus copy — the same
    isolation contract as ``benchmarks.validate_workers``.
    """

    def __init__(self) -> None:
        self._servers: list[Any] = []
        self._ports: list[int] = []
        self._workdirs: list[Path] = []

    def spawn(self, data_dir: Path, state_root: Path, port: int, workers: int = 1) -> None:
        from benchmarks.validate_workers import _spawn_api

        proc = _spawn_api(data_dir, state_root, port, workers=workers)
        self._servers.append(proc)
        self._ports.append(port)
        self._workdirs.append(state_root)

    def pop(self, port: int) -> None:
        """Shut down and remove the server bound to *port* (no-op if absent).

        Used by the recovery section to restart its own server: sections
        share one pool in ``run_gate``, so popping by *port* (instead of
        popping the last element, which may belong to a different
        section) is order-independent and safe.
        """
        from benchmarks.validate_workers import _shutdown  # noqa: PLC0415

        for i in range(len(self._servers) - 1, -1, -1):
            if self._ports[i] == port:
                _shutdown(self._servers[i])
                self._servers.pop(i)
                self._ports.pop(i)
                self._workdirs.pop(i)
                return

    def wait_ready(self, port: int, timeout: float = 120.0) -> str:
        return _wait_ready(port, timeout)

    def shutdown_all(self) -> None:
        from benchmarks.validate_workers import _shutdown

        for proc in self._servers:
            _shutdown(proc)
        self._servers.clear()
        self._ports.clear()
        self._workdirs.clear()


def _close_pool(pool: ServerPool | None, owned: bool) -> None:
    """Shut down *pool* only when the calling section created it.

    Sections accept an optional shared pool (``run_gate`` passes one).
    Since the Sprint 13 per-section teardown fix every section shuts
    down its *own* server via ``_close_section`` before returning, so
    ``_close_pool`` is the safety net for the standalone case where the
    section created the pool itself — otherwise a direct call would
    leak a uvicorn process.
    """
    if owned and pool is not None:
        pool.shutdown_all()


def _close_section(pool: ServerPool | None, owned: bool, port: int) -> None:
    """Tear down the calling section's *own* server, then the pool if owned.

    Sections share one pool in ``run_gate``; if every section left its
    server alive until the end, servers accumulate and later sections
    measure with 3+ co-resident uvicorn processes competing for CPU.
    Measured 2026-08-12: the multi-worker section's 8-way barrage
    timed out on 4/8 requests (60 s each) under 3 co-resident gate
    servers plus an orphan, yet passes 8/8 standalone on a quiet
    machine.  Every section therefore shuts down *its own* server on
    exit (``pool.pop(port)`` is order-independent and a no-op when the
    section failed before spawning), so each section measures a quiet
    machine — the honest baseline, matching the documented
    performance-section rationale.
    """
    if pool is not None:
        pool.pop(port)
    _close_pool(pool, owned)


# ───────────────────────────────────────────────────────────────────
# Section implementations
# ───────────────────────────────────────────────────────────────────

def section_core_pipeline(
    synthetic: int = 12,
    real: int = 12,
    tmp: Path | None = None,
) -> Section:
    """In-process: DataService -> scanner -> indicators -> analyzer ->
    signals -> ranking -> alerts over synthetic + real corpora."""
    from benchmarks.common import write_csvs  # noqa: PLC0415
    from benchmarks.pipeline import _state_files  # noqa: PLC0415
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.engine.analyzer import analyze_stock  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415
    from src.scanner.ranking import rank_market  # noqa: PLC0415

    sec = Section("core_pipeline")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_core_"))
    try:
        data_dir = tmp / "data"
        write_csvs(data_dir, synthetic, 300)
        with _state_files(tmp):
            old_dir = scanner_engine.DATA_DIRECTORY
            scanner_engine.DATA_DIRECTORY = str(data_dir)
            try:
                scanner_cache.clear()
                out = scanner_engine.scan_market(workers=2)
                results = out.get("results", [])
                skipped = out.get("skipped", [])
                if not results:
                    return sec.fail("scan_market returned no results")
                if skipped:
                    return sec.fail(f"scan_market skipped {len(skipped)} symbols")
                required = {"price", "signal", "score", "confidence", "trend", "rsi"}
                missing = [
                    f"{r.get('symbol')}:{k}" for r in results for k in required
                    if r.get(k) is None
                ]
                if missing:
                    return sec.fail(f"analysis missing required fields: {missing[:5]}")

                # Ranking determinism: stable sort must be reproducible.
                ranked_again = rank_market(results)
                if [r["symbol"] for r in results] != [r["symbol"] for r in ranked_again]:
                    return sec.fail("rank_market is not deterministic")

                # Single-symbol alert path through the real analyzer.
                first = analyze_stock(str(sorted(data_dir.glob("*.csv"))[0]))
                if "new_alerts" not in first:
                    return sec.fail("analyze_stock did not attach new_alerts")
            finally:
                scanner_engine.DATA_DIRECTORY = old_dir

        # Real-corpus leg (audit-gated, read-only, copied into tmp).
        from benchmarks import corpus as corpus_mod  # noqa: PLC0415
        from benchmarks.corpus_audit import audit_corpus  # noqa: PLC0415

        real_dir = tmp / "real_data"
        audit = audit_corpus(corpus_mod.DEFAULT_RAW_DIR)
        usable = audit.get("usable", 0)
        if usable == 0:
            sec.note("real corpus unavailable (usable=0); real leg skipped")
        else:
            spec = corpus_mod.select_corpus(real, seed=42)
            copied = corpus_mod.copy_corpus(spec, real_dir)
            if not copied:
                return sec.fail("real corpus selection copied no files")
            with _state_files(tmp / "real_state"):
                old_dir = scanner_engine.DATA_DIRECTORY
                scanner_engine.DATA_DIRECTORY = str(real_dir)
                try:
                    scanner_cache.clear()
                    real_out = scanner_engine.scan_market(workers=2)
                    if not real_out.get("results"):
                        return sec.fail("real-corpus scan returned no results")
                    errs = [
                        r["symbol"] for r in real_out["results"]
                        if r.get("score") is None
                    ]
                    if errs:
                        return sec.fail(f"real-corpus analyses missing score: {errs[:5]}")
                    sec.details["real_symbols_analysed"] = len(real_out["results"])
                    sec.details["real_skipped"] = len(real_out.get("skipped", []))
                    sec.details["corpus_audit"] = {
                        "file_count": audit["file_count"],
                        "usable": usable,
                    }
                finally:
                    scanner_engine.DATA_DIRECTORY = old_dir

        sec.details["synthetic_symbols"] = synthetic
        sec.details["synthetic_analysed"] = len(results)
        return sec.pass_()
    finally:
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def _analyse_over_http(port: int, symbol: str) -> dict[str, Any] | None:
    status, body = _request(port, f"/analyze/{symbol}")
    if status != 200:
        return None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _stable_subset(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: payload.get(k) for k in STABLE_FIELDS if k in payload}


def section_api_live(
    synthetic: int = 10,
    port: int = 8901,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """Real single-worker uvicorn server; every critical endpoint over HTTP."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("api_live")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_api_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API did not become ready: {err}")

        symbol = "SYN000"
        checks: dict[str, Any] = {}

        # Health + metrics
        status, _ = _request(port, "/")
        checks["health"] = status
        status, body = _request(port, "/metrics")
        metrics = {}
        if status == 200:
            try:
                metrics = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                metrics = {}
        checks["metrics"] = status

        # Analyze: cold -> warm -> repeated (stable equivalence).
        cold = _analyse_over_http(port, symbol)
        warm = _analyse_over_http(port, symbol)
        repeat = _analyse_over_http(port, symbol)
        if cold is None or warm is None or repeat is None:
            return sec.fail(" /analyze/{symbol} did not return 200 JSON")
        checks["analyze_stable_cold_warm"] = _stable_subset(cold) == _stable_subset(warm)
        checks["analyze_stable_warm_repeat"] = _stable_subset(warm) == _stable_subset(repeat)
        checks["analyze_has_indicators"] = all(
            cold.get(k) is not None for k in ("rsi", "macd", "atr", "trend", "support", "resistance")
        )

        # Invalid input: path traversal / bad format must be 400/404, never 500.
        status, _ = _request(port, "/analyze/..%2Fetc")
        checks["analyze_invalid"] = status
        status, _ = _request(port, "/analyze/__NO_SUCH_SYMBOL_12345__")
        checks["analyze_unknown"] = status

        # Watchlist CRUD + scan (real mutation through the real endpoint).
        status, _ = _request(port, "/watchlist")
        checks["watchlist_get"] = status
        status, _ = _request(port, "/watchlist/add/" + symbol, method="POST")
        checks["watchlist_add"] = status
        status, _ = _request(port, "/watchlist/scan")
        checks["watchlist_scan"] = status
        status, _ = _request(port, "/watchlist/remove/" + symbol, method="DELETE")
        checks["watchlist_remove"] = status
        status, _ = _request(port, "/watchlist/add/bad..sym", method="POST")
        checks["watchlist_invalid"] = status

        # Portfolio (empty portfolio is a valid 200 in this engine).
        status, _ = _request(port, "/portfolio")
        checks["portfolio"] = status

        # Market scanner endpoints (real scan against the private corpus).
        status, _ = _request(port, "/market/top10")
        checks["market_top10"] = status

        # Only HTTP status ints count as endpoint checks; the boolean
        # equivalence flags above must never be treated as statuses
        # (``bool`` subclasses ``int``, so exclude it explicitly).
        bad = {
            k: v for k, v in checks.items()
            if isinstance(v, int) and not isinstance(v, bool) and v not in (200, 400, 404)
        }
        if bad:
            return sec.fail(f"unexpected endpoint statuses: {bad}")

        # Invalid-input semantics: path traversal and unknown symbol must
        # be 4xx (not 500); traversal must be rejected (400), unknown 404.
        if checks["analyze_invalid"] != 400 and checks["analyze_invalid"] != 404:
            return sec.fail(f"invalid /analyze input returned {checks['analyze_invalid']}")
        if checks["analyze_unknown"] != 404:
            return sec.fail(f"unknown symbol returned {checks['analyze_unknown']}")

        sec.details["endpoint_statuses"] = checks
        sec.details["analyze_cold_price"] = cold.get("price")
        sec.details["metrics_keys"] = sorted(metrics.keys())[:8]
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_cache(
    synthetic: int = 10,
    port: int = 8902,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """Cold/warm equivalence on stable fields + observable cache counters."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("cache")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_cache_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API did not become ready: {err}")

        # A fresh server has cold caches: warm it with one pass, then
        # compare cold vs warm analysis on the same symbol.
        symbol = "SYN000"
        cold = _analyse_over_http(port, symbol)
        # Warm the whole corpus so indicator/scanner caches are populated.
        for i in range(synthetic):
            _analyse_over_http(port, f"SYN{i:03d}")
        warm = _analyse_over_http(port, symbol)
        if cold is None or warm is None:
            return sec.fail("/analyze failed during cache warm-up")

        equivalent = _stable_subset(cold) == _stable_subset(warm)
        if not equivalent:
            diff = {k: (cold.get(k), warm.get(k)) for k in STABLE_FIELDS if cold.get(k) != warm.get(k)}
            return sec.fail(f"cold/warm analysis diverged on stable fields: {diff}")

        # Observable cache counters from /metrics (not inferred from time).
        # NOTE: /api/analyze exercises the scanner *dataframe* tier (via
        # load_csv) and the indicator cache — it never touches the scanner
        # *analysis* tier (get_analysis/put_analysis is only exercised by
        # scan_market, covered by the core_pipeline section).  Assert the
        # tiers this endpoint actually touches.
        _status, body = _request(port, "/metrics")
        metrics = json.loads(body) if body else {}
        scan_stats = metrics.get("scanner_cache") or {}
        ind_stats = metrics.get("indicator_cache") or {}
        df_hits = int(scan_stats.get("dataframe_hits", 0))
        df_misses = int(scan_stats.get("dataframe_misses", 0))
        ind_hits = int(ind_stats.get("hits", 0))
        if df_hits <= 0:
            return sec.fail("scanner dataframe cache reported zero hits after warm-up")
        if ind_hits <= 0:
            return sec.fail("indicator cache reported zero hits after warm-up")
        sec.details["scanner_dataframe_hits"] = df_hits
        sec.details["scanner_dataframe_misses"] = df_misses
        sec.details["scanner_analysis_hits"] = int(scan_stats.get("analysis_hits", 0))
        sec.details["scanner_analysis_misses"] = int(scan_stats.get("analysis_misses", 0))
        sec.details["scanner_hit_rate"] = scan_stats.get("cache_hit_rate")
        sec.details["indicator_cache"] = ind_stats
        sec.details["warm_request"] = "repeated /analyze returns identical stable fields"
        sec.note(
            "scanner analysis tier is exercised by scan_market (core_pipeline), "
            "not by /api/analyze"
        )
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_persistence(tmp: Path | None = None) -> Section:
    """Concurrent writes, atomicity, corruption recovery, temp/lock cleanup."""
    from src.utils import json_store  # noqa: PLC0415

    sec = Section("persistence")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_persist_"))
    try:
        watch = tmp / "watchlist.json"

        # ── Concurrent writes (threads, same file) ─────────────────
        n_threads = 8
        errors: list[str] = []
        barrier = threading.Barrier(n_threads)

        def _writer(i: int) -> None:
            try:
                barrier.wait(timeout=30)

                def _mutate(data):
                    data[f"SYM{i}"] = {"enabled": True}
                    return data

                json_store.update_json(watch, _mutate, {}, log_name="ProdGate")
            except Exception as exc:  # noqa: BLE001 - thread isolation
                errors.append(str(exc))

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        if errors:
            return sec.fail(f"concurrent writes failed: {errors[:3]}")

        final = json_store.load_json(watch, {})
        if not isinstance(final, dict) or len(final) != n_threads:
            return sec.fail(
                f"lost update: expected {n_threads} symbols, got {len(final) if isinstance(final, dict) else 'non-dict'}"
            )

        # ── Atomicity / cleanup: no .tmp / .lock leftovers ────────
        tmp_files = sorted(str(p.name) for p in tmp.rglob("*.tmp"))
        lock_files = sorted(str(p.name) for p in tmp.rglob("*.lock"))
        if tmp_files:
            return sec.fail(f"leftover .tmp files: {tmp_files}")
        if lock_files:
            return sec.fail(f"leftover .lock files: {lock_files}")

        # ── Corruption recovery: malformed JSON is backed up, default returned ──
        corrupt = tmp / "corrupt.json"
        corrupt.write_text("{ this is not json", encoding="utf-8")
        result = json_store.load_json(corrupt, {"recovered": True}, log_name="ProdGate")
        backups = [p for p in tmp.glob("corrupt*.bak")]
        if result != {"recovered": True}:
            return sec.fail("load_json did not return the default for a corrupt file")
        if not backups:
            return sec.fail("corrupt file was not preserved as .corrupt.bak")

        sec.details["concurrent_writers"] = n_threads
        sec.details["symbols_survived"] = len(final)
        sec.details["corrupt_backup"] = backups[0].name
        sec.details["leftover_tmp"] = 0
        sec.details["leftover_locks"] = 0
        return sec.pass_()
    finally:
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_multi_worker(
    synthetic: int = 10,
    port: int = 8894,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """uvicorn --workers=2 (Docker api stage) with concurrent traffic."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("multi_worker")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_mw_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=2)
        err = pool.wait_ready(port, timeout=120)
        if err:
            return sec.fail(f"2-worker API did not become ready: {err}")

        # Concurrent barrage across endpoints.  Evidence-carrying: a
        # timed-out request records the exception so a future failure
        # is diagnosable instead of a bare count (Sprint 13 review fix).
        barrier = threading.Barrier(8, timeout=30)
        outcomes: list[int] = []
        errs: list[str] = []

        def _hit(i: int) -> None:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                errs.append(f"thread {i}: barrier broke (peer never arrived)")
                return
            path = "/metrics" if i % 2 else f"/analyze/SYN{i:03d}"
            try:
                status, _ = _request(port, path)
                outcomes.append(status)
            except Exception as exc:  # noqa: BLE001 - record evidence
                errs.append(f"{path}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=_hit, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        if len(outcomes) != 8:
            detail = f"; first error: {errs[0]}" if errs else ""
            return sec.fail(
                f"only {len(outcomes)}/8 concurrent requests completed{detail}"
            )
        if set(outcomes) != {200}:
            return sec.fail(f"non-200 outcomes: {sorted(set(outcomes))}")

        # Worker identity + aggregation sanity from /metrics.
        pids: set[int] = set()
        agg_hits = agg_misses = local_hits = local_misses = 0
        for _ in range(6):
            status, body = _request(port, "/metrics")
            if status != 200:
                continue
            try:
                m = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
            pid = (m.get("process") or {}).get("pid")
            if isinstance(pid, int):
                pids.add(pid)
            agg = m.get("aggregate") or {}
            if isinstance(agg, dict):
                agg_hits = max(agg_hits, int(agg.get("hits", 0)))
                agg_misses = max(agg_misses, int(agg.get("misses", 0)))
            ind = m.get("indicator_cache") or {}
            if isinstance(ind, dict):
                local_hits = max(local_hits, int(ind.get("hits", 0)))
                local_misses = max(local_misses, int(ind.get("misses", 0)))

        if len(pids) < 2:
            sec.note(
                f"observed {len(pids)} distinct worker pid(s) across 6 /metrics calls "
                "(load balancer may route to one worker; not a failure)"
            )
        # Sum semantics: the aggregate is the sum across workers, so it
        # must be >= any single worker's local counters.
        if agg_hits < local_hits or agg_misses < local_misses:
            return sec.fail(
                f"aggregate < local (agg hits {agg_hits} < local {local_hits}) — "
                "aggregation violated sum semantics"
            )
        sec.details["workers_seen"] = sorted(pids)
        sec.details["concurrent_requests"] = len(outcomes)
        sec.details["aggregate_hits"] = agg_hits
        sec.details["local_hits_max"] = local_hits
        sec.details["aggregate_superset_of_local"] = True
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_concurrency(
    synthetic: int = 10,
    port: int = 8903,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """5 and 10 concurrent requests; success rate, failures, latency."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("concurrency")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_conc_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API did not become ready: {err}")

        results: dict[str, Any] = {}
        for n in (5, 10):
            lat: list[float] = []
            statuses: list[int] = []
            errs: list[str] = []
            barrier = threading.Barrier(n, timeout=30)
            lock = threading.Lock()

            def _hit(i: int) -> None:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    with lock:
                        errs.append(f"thread {i}: barrier broke")
                    return
                start = time.perf_counter()
                path = "/metrics" if i % 3 == 0 else f"/analyze/SYN{i % synthetic:03d}"
                try:
                    status, _ = _request(port, path)
                except Exception as exc:  # noqa: BLE001 - record evidence
                    with lock:
                        errs.append(f"{path}: {type(exc).__name__}: {exc}")
                    status = -1
                with lock:
                    # Failure sample intentionally included: the timer
                    # ran through the timeout, so the latency percentile
                    # is not falsely clean when requests fail.
                    lat.append((time.perf_counter() - start) * 1000.0)
                    statuses.append(status)

            threads = [threading.Thread(target=_hit, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)
            results[n] = {
                "completed": len(statuses),
                "success": statuses.count(200),
                "failures": len(statuses) - statuses.count(200),
                "latency": _percentiles(lat),
            }
            if len(statuses) != n or statuses.count(200) != n:
                detail = f"; first error: {errs[0]}" if errs else ""
                return sec.fail(f"{n}-concurrent smoke failed: {results[n]}{detail}")

        sec.details["runs"] = results
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────
# BOT section — boots the real telegram bot against the live API
# ───────────────────────────────────────────────────────────────────
# The bot is a polling application: ``main()`` builds an ``Application``
# with ``TELEGRAM_TOKEN`` and calls ``run_polling()``.  A hermetic gate
# cannot poll Telegram (needs a real token + live network), so the
# section boots the *module* in a fresh subprocess with ``API_BASE_URL``
# pointed at this section's live server, builds the real ``Application``
# exactly as ``main()`` does, and invokes every command handler with a
# minimal recording Update/Context double.  The handlers themselves are
# unmodified production code — each one makes its *real* HTTP request to
# the live API (``requests.get(f"{API_BASE}/...")``).  Live polling is
# explicitly not exercised; that is documented in the section's notes,
# never hidden.
_BOT_CHECK_SCRIPT = r'''"""Hermetic bot-to-API contract check (spawned by section_bot).

Boots the real ``src.bot.telegram_bot`` module — the same code the
Docker ``bot`` service runs — against the live gate API.  Telegram
transport (``run_polling``) cannot be exercised without a real token
and network, so handlers are invoked with a minimal recording
``Update``/``Context`` double while every handler makes its *real*
HTTP call to the live server.

Prints one JSON object to stdout::

    {
      "registered": ["analyze", "buylist", ...],   # Application wiring
      "start":      {"replies": [...], "error": null},
      ...
      "token":      "injected" | "dummy"
    }
"""
import asyncio
import json
import os
import socket
import sys

socket.setdefaulttimeout(30)

# The gate pipes this probe's stdout (``subprocess.PIPE``), so on Windows
# ``sys.stdout`` falls back to the locale encoding (cp1252) and printing
# emoji-laden bot replies (📊 🟢 🏆) would raise UnicodeEncodeError →
# rc=1 → the section FAILs on a healthy machine.  Mirror the gate's own
# ``main()`` stream reconfigure so the JSON payload is always UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from src.bot import telegram_bot as bot  # noqa: E402
from telegram.ext import Application, CommandHandler  # noqa: E402


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self) -> None:
        self.message = _FakeMessage()


class _FakeContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


async def _run(handler, args: list[str] | None = None) -> dict:
    update = _FakeUpdate()
    try:
        await handler(update, _FakeContext(args))
        return {"replies": list(update.message.replies), "error": None}
    except Exception as exc:  # noqa: BLE001 - record evidence, never crash the probe
        return {
            "replies": list(update.message.replies),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN", "") or "123456:TEST-DUMMY"
    app = Application.builder().token(token).build()
    handlers = {
        "start": bot.start,
        "help": bot.help_command,
        "analyze": bot.analyze,
        "backtest": bot.backtest,
        "signals": bot.signals,
        "top10": bot.top10,
        "buylist": bot.buylist,
        "selllist": bot.selllist,
        "strongbuy": bot.strongbuy,
        "market": bot.market,
        "watchlist": bot.watchlist,
        "portfolio": bot.portfolio,
    }
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))
    registered = sorted(
        {
            c
            for group in app.handlers.values()
            for h in group
            for c in getattr(h, "commands", ())
        }
    )
    out: dict = {
        "registered": registered,
        "token": "injected" if os.environ.get("TELEGRAM_TOKEN") else "dummy",
    }
    out["start"] = await _run(bot.start)
    out["help"] = await _run(bot.help_command)
    out["analyze"] = await _run(bot.analyze, ["SYN000"])
    out["backtest"] = await _run(bot.backtest, ["SYN000"])
    out["signals"] = await _run(bot.signals, ["SYN000"])
    out["top10"] = await _run(bot.top10)
    out["buylist"] = await _run(bot.buylist)
    out["selllist"] = await _run(bot.selllist)
    out["strongbuy"] = await _run(bot.strongbuy)
    out["market"] = await _run(bot.market)
    out["watchlist"] = await _run(bot.watchlist)
    # ``/watchlist scan`` is an argument-dispatched sub-command of the
    # ``watchlist`` handler — probed separately so both branches are
    # covered by the contract check.
    out["watchlist_scan"] = await _run(bot.watchlist, ["scan"])
    out["portfolio"] = await _run(bot.portfolio)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
'''


def _validate_bot_results(results: dict[str, Any]) -> tuple[bool, str]:
    """Validate the bot-check subprocess JSON (pure helper, unit-tested).

    Returns ``(True, "")`` when every command is registered and each
    handler reply is well-formed; ``(False, reason)`` otherwise.

    ``watchlist_scan`` is not a registered command — it is the
    ``/watchlist scan`` argument-dispatched branch of the ``watchlist``
    handler — so it is probed (and checked) separately from the
    registered set.
    """
    expected = {
        "start", "help", "analyze", "backtest", "signals", "top10",
        "buylist", "selllist", "strongbuy", "market", "watchlist",
        "portfolio",
    }
    registered = set(results.get("registered", []))
    if registered != expected:
        return False, f"registered commands mismatch: {sorted(registered)}"

    # Each command: (required substrings, forbidden substrings,
    # documented empty-state replies).  Forbidden replies are genuine
    # failures (e.g. the pre-fix /analyze ``"Error processing request."``
    # fallback); empty-state replies (``"No BUY signals today."``,
    # ``"Watchlist is empty."``) are valid handler outcomes, not
    # failures.
    markers: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
        "start": (("Available Commands", "/analyze"), (), ()),
        "help": (("Available Commands",), (), ()),
        "analyze": (("SYN000", "Signal:"), (), ()),
        "backtest": (("Backtest", "SYN000"), (), ("No trades generated",)),
        "signals": (("SYN000", "Signal:"), (), ()),
        "top10": (("Top 10",), (), ()),
        "buylist": (("BUY",), (), ("No BUY signals today.",)),
        "selllist": (("SELL",), (), ("No SELL signals today.",)),
        "strongbuy": (("STRONG BUY",), (), ("No STRONG BUY signals today.",)),
        "market": (("NEPSE Market Summary",), (), ()),
        "watchlist": (("Watchlist",), (), ()),
        "watchlist_scan": (
            ("Watchlist Scan",),
            (),
            ("Watchlist is empty — add symbols first.",),
        ),
        "portfolio": (("Portfolio Summary",), (), ()),
    }

    for cmd in sorted(markers):
        entry = results.get(cmd) or {}
        if entry.get("error"):
            return False, f"/{cmd} handler raised: {entry['error']}"
        replies = entry.get("replies") or []
        if not replies:
            return False, f"/{cmd} produced no reply"
        joined = " | ".join(replies)
        # The generic error fallback is a failure for EVERY command — it
        # is the broad-except reply the pre-fix /analyze fell into, so it
        # must never be accepted anywhere, even if a required marker also
        # appears.
        if "Error processing request." in joined:
            return False, (
                f"/{cmd} returned the generic error reply: {joined[:80]!r}"
            )
        required, forbidden, empty_variants = markers[cmd]
        # A command-specific forbidden reply is always a failure — even
        # if a required marker also appears.
        for marker in forbidden:
            if marker in joined:
                return False, f"/{cmd} returned forbidden reply {marker!r}: {joined[:80]!r}"
        if any(v in joined for v in empty_variants):
            # A documented empty-state reply is a valid handler outcome.
            continue
        if not any(m in joined for m in required):
            return False, f"/{cmd} reply missing {required}: {joined[:80]!r}"
    return True, ""


def section_bot(
    synthetic: int = 6,
    port: int = 8910,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """Real bot module boots against the live API; all 12 handlers verified.

    Covers the 12 registered commands (``/start``, ``/help``, ``/analyze``,
    ``/backtest``, ``/signals``, ``/top10``, ``/buylist``, ``/selllist``,
    ``/strongbuy``, ``/market``, ``/watchlist``, ``/portfolio``) plus the
    argument-dispatched ``/watchlist scan`` branch.  Telegram transport
    itself is not exercised (needs a real token + network) — documented
    in the section notes.  Everything else runs: the real module, the
    real ``Application`` wiring, and every command handler's real HTTP
    call to this section's live server.
    """
    import importlib.util  # noqa: PLC0415

    sec = Section("bot")
    if importlib.util.find_spec("telegram") is None:
        return sec.skip("python-telegram-bot not installed — bot module cannot be validated")

    from benchmarks.common import write_csvs  # noqa: PLC0415

    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_bot_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    bot_proc: subprocess.Popen | None = None
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)
        # Case-safety: the bot lower-cases its symbol argument and the
        # API resolves ``<symbol>.csv`` lower-cased, so the analyze
        # target must exist lower-case (matters on case-sensitive
        # filesystems such as Linux CI).  ``scan_market`` derives the
        # symbol from ``stem.upper()``, so the symbol stays ``SYN000``
        # while the lower-case file resolves everywhere.
        # ``benchmarks.common.write_csvs`` now writes lower-case names
        # directly; the rename below is kept purely as a safety net for
        # stale fixture copies.  The probe's hard-coded analyze arg
        # ``["SYN000"]`` in ``_BOT_CHECK_SCRIPT`` stays in sync with
        # the symbol derivation (``stem.upper()``).
        target = data_dir / "syn000.csv"
        if not target.exists():
            legacy = data_dir / "SYN000.csv"
            if legacy.exists():
                legacy.rename(target)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API did not become ready for bot section: {err}")

        script = tmp / "bot_check.py"
        script.write_text(_BOT_CHECK_SCRIPT, encoding="utf-8")
        env = dict(os.environ)
        env.update(_offline_provider_env())
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["API_BASE_URL"] = f"http://127.0.0.1:{port}"
        env["DATA_DIRECTORY"] = str(data_dir)
        env["NEPSE_HOME"] = str(tmp / "nepse_home")
        token = os.environ.get("TELEGRAM_TOKEN", "")
        if token:
            env["TELEGRAM_TOKEN"] = token
        bot_proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(state_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = bot_proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            bot_proc.kill()
            return sec.fail("bot check subprocess timed out (120 s)")
        if bot_proc.returncode != 0:
            return sec.fail(
                f"bot check subprocess exited rc={bot_proc.returncode}: "
                f"{stderr[-400:] if stderr else 'no stderr'}"
            )
        try:
            results = json.loads(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            return sec.fail(f"bot check output was not JSON: {exc}; stdout={stdout[:200]!r}")

        ok, why = _validate_bot_results(results)
        if not ok:
            return sec.fail(why)
        sec.details["token_injected"] = results.get("token") == "injected"
        sec.details["registered_commands"] = results.get("registered")
        sec.details["replies"] = {
            cmd: (results.get(cmd) or {}).get("replies", [""])[0][:60]
            for cmd in ("start", "analyze", "market")
            if (results.get(cmd) or {}).get("replies")
        }
        if not sec.details["token_injected"]:
            sec.note(
                "TELEGRAM_TOKEN not set in environment — Application built with a "
                "dummy token; handler-to-API contract verified, live Telegram "
                "polling not exercised (needs a real token + network)"
            )
        else:
            sec.note(
                "Real TELEGRAM_TOKEN injected; Application wiring verified. Live "
                "Telegram polling still not exercised (network-dependent)"
            )
        return sec.pass_()
    finally:
        if bot_proc is not None and bot_proc.poll() is None:
            bot_proc.kill()
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_streamlit(
    api_port: int = 8906,
    web_port: int = 8907,
    api_pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """Real Streamlit app boots headless; health OK; Metrics page HTTP path works."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("streamlit")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_stream_"))
    owned_pool = api_pool is None
    api_pool = api_pool or ServerPool()
    streamlit_proc: subprocess.Popen | None = None
    try:
        # Backend: single-worker API on the private corpus.
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, 6, 300)
        if not _free_port(api_port):
            return sec.fail(f"api port {api_port} already bound")
        api_pool.spawn(data_dir, state_root, api_port, workers=1)
        err = api_pool.wait_ready(api_port)
        if err:
            return sec.fail(f"API did not become ready: {err}")

        if not _free_port(web_port):
            return sec.fail(f"streamlit port {web_port} already bound")

        env = dict(os.environ)
        env.update(_offline_provider_env())
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["DATA_DIRECTORY"] = str(data_dir)
        env["NEPSE_HOME"] = str(tmp / "nepse_home")
        env["API_BASE_URL"] = f"http://127.0.0.1:{api_port}"
        env["STREAMLIT_SERVER_HEADLESS"] = "true"
        # cwd MUST be the private state root: the watchlist/portfolio/
        # alert-history stores resolve against the process CWD
        # (``Path("data/watchlist/watchlist.json")`` etc.), so running
        # Streamlit with cwd=PROJECT_ROOT would write real state files.
        # cwd stays at the private state root (so relative ``Path("data/...")
        # stores land in the temp tree) but the script path must be
        # absolute — ``streamlit run app.py`` with cwd=state_root cannot
        # find the app and the process exits at startup.
        # stderr goes to a log file under the private tmp root so a
        # startup failure is diagnosable (exit code + last lines) instead
        # of a bare "process exited" with zero evidence.
        stderr_log = tmp / "streamlit_stderr.log"
        streamlit_proc = subprocess.Popen(
            [
                sys.executable, "-m", "streamlit", "run",
                str(PROJECT_ROOT / "app.py"),
                "--server.address=127.0.0.1",
                "--server.port", str(web_port),
                "--server.headless=true",
                "--browser.gatherUsageStats=false",
            ],
            cwd=str(state_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr_log.open("wb"),
        )

        # Streamlit health endpoint (/_stcore/health).
        deadline = time.monotonic() + 120
        healthy = False
        last = "not attempted"
        while time.monotonic() < deadline:
            if streamlit_proc.poll() is not None:
                tail = ""
                try:
                    tail = stderr_log.read_text(encoding="utf-8", errors="replace")[-600:]
                except OSError:
                    pass
                return sec.fail(
                    f"Streamlit process exited during startup (rc={streamlit_proc.returncode})"
                    + (f"; stderr tail: {tail!r}" if tail.strip() else "")
                )
            try:
                import urllib.request

                with urllib.request.urlopen(  # noqa: S310 - loopback
                    f"http://127.0.0.1:{web_port}/_stcore/health", timeout=5.0
                ) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception as exc:  # noqa: BLE001 - startup probing
                last = str(exc)
            time.sleep(1.0)
        if not healthy:
            return sec.fail(f"Streamlit health endpoint did not answer: {last}")

        # Metrics page HTTP path: fetch_metrics against the live backend.
        from src.ui.pages.metrics_page import fetch_metrics, summarize  # noqa: PLC0415

        payload = fetch_metrics(base_url=f"http://127.0.0.1:{api_port}", timeout=5.0)
        summary = summarize(payload)
        if summary["workers_total"] < 1:
            return sec.fail("metrics page reported zero workers against a live backend")
        sec.details["streamlit_health"] = "200"
        sec.details["metrics_page_fetch"] = {
            "workers_observed": summary["workers_total"],
            "status": summary["status"],
        }
        return sec.pass_()
    except Exception as exc:  # noqa: BLE001 - section isolation
        return sec.fail(f"streamlit section raised: {exc}")
    finally:
        _close_section(api_pool, owned_pool, api_port)
        if streamlit_proc is not None and streamlit_proc.poll() is None:
            try:
                streamlit_proc.terminate()
                streamlit_proc.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                streamlit_proc.kill()
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_performance(tmp: Path | None = None) -> Section:
    """Existing CI regression gate + scanner warm baseline + cache capacity."""
    sec = Section("performance")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_perf_"))
    try:
        from benchmarks import ci_gate  # noqa: PLC0415
        from src.config import SCANNER_CACHE_MAX_ENTRIES  # noqa: PLC0415
        from benchmarks import corpus as corpus_mod  # noqa: PLC0415
        from benchmarks.corpus_audit import audit_corpus  # noqa: PLC0415

        out = tmp / "benchmark-ci.json"
        passed, artifact = ci_gate.run_gate(out_file=out)
        if not passed:
            fails = {k for k, v in (artifact.get("checks") or {}).items() if not v}
            return sec.fail(f"CI regression gate failed: {sorted(fails)}")
        sec.details["ci_gate"] = {
            "checks": artifact.get("checks"),
            "cold_load_best_ms": artifact.get("cold_load_best_ms"),
            "warm_speedup_x": artifact.get("cold_load_warm_speedup_x"),
            "analyze_warm_p99_ms": artifact.get("api_analyze_warm_p99_ms"),
            "warm_scan_ratio": artifact.get("scanner_warm_cold_ratio"),
        }

        # Sprint 12.3 guarantee: default cache capacity covers the corpus.
        usable = audit_corpus(corpus_mod.DEFAULT_RAW_DIR).get("usable", 0)
        if usable and SCANNER_CACHE_MAX_ENTRIES < usable:
            return sec.fail(
                f"SCANNER_CACHE_MAX_ENTRIES ({SCANNER_CACHE_MAX_ENTRIES}) < real corpus ({usable}) — "
                "warm scans would silently re-parse the corpus (Sprint 12.3 finding)"
            )
        sec.details["cache_capacity"] = {
            "SCANNER_CACHE_MAX_ENTRIES": SCANNER_CACHE_MAX_ENTRIES,
            "real_corpus_usable": usable,
            "covers_corpus": usable <= SCANNER_CACHE_MAX_ENTRIES,
        }
        return sec.pass_()
    finally:
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_recovery(
    synthetic: int = 8,
    port: int = 8904,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """API restart, corrupt optional data, per-symbol failure isolation."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("recovery")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_rec_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)

        # ── Per-symbol failure isolation: a corrupt CSV must not break the scan ──
        from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
        from src.scanner import engine as scanner_engine  # noqa: PLC0415
        from benchmarks.pipeline import _state_files  # noqa: PLC0415

        corrupt_dir = tmp / "corrupt_data"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        write_csvs(corrupt_dir, 4, 200)
        (corrupt_dir / "BROKEN.csv").write_text("Date,Open,High,Low,Close,Volume\nnot,a,valid,row,here,x\n", encoding="utf-8")
        with _state_files(tmp / "rec_state"):
            old_dir = scanner_engine.DATA_DIRECTORY
            scanner_engine.DATA_DIRECTORY = str(corrupt_dir)
            try:
                scanner_cache.clear()
                out = scanner_engine.scan_market(workers=2)
                if not out.get("results"):
                    return sec.fail("scan with one corrupt CSV produced no results")
                broken_isolated = any(
                    s.get("symbol") == "BROKEN" for s in out.get("skipped", [])
                )
                healthy_count = len(out["results"])
                if healthy_count < 3:
                    return sec.fail("corrupt CSV broke healthy symbols' analyses")
                sec.details["corrupt_symbol_isolated"] = broken_isolated
                sec.details["healthy_analysed"] = healthy_count
            finally:
                scanner_engine.DATA_DIRECTORY = old_dir

        # ── Corrupt optional data: API still serves after history.json is mangled ──
        history = state_root / "alerts" / "history.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text("{ corrupted", encoding="utf-8")
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API with corrupt alert history did not start: {err}")
        status, _ = _request(port, "/")
        if status != 200:
            return sec.fail(f"API unhealthy after corrupt history: {status}")

        # ── API restart: kill + respawn must return to a healthy state ──
        pool.pop(port)
        if not _free_port(port):
            return sec.fail(f"port {port} not freed after shutdown")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API did not recover after restart: {err}")
        status, _ = _request(port, "/analyze/SYN000")
        if status != 200:
            return sec.fail(f"API /analyze unhealthy after restart: {status}")
        sec.details["restart_recovered"] = True
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def audit_source_scan(src_root: Path | None = None) -> dict[str, list[str]]:
    """AST scan of ``src`` for unsafe deserialisation, eval/exec, secrets.

    Pure helper (no network, no state) so the security section and unit
    tests share one implementation.  Returns ``{pickle_load, eval_exec,
    hardcoded_secret}`` lists of ``path:lineno`` hits.
    """
    import ast  # noqa: PLC0415
    import re  # noqa: PLC0415

    src_root = src_root or Path(PROJECT_ROOT / "src")
    bad: dict[str, list[str]] = {"pickle_load": [], "eval_exec": [], "hardcoded_secret": []}
    secret_name = re.compile(
        r"(token|secret|password|passwd|api[_-]?key|credential)", re.IGNORECASE
    )

    def _is_pickle(node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id == "pickle"
        return False

    for p in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("load", "loads") and _is_pickle(node):
                    bad["pickle_load"].append(f"{p}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("eval", "exec"):
                    bad["eval_exec"].append(f"{p}:{node.lineno}")
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                and secret_name.search(node.targets[0].id)
                and isinstance(node.value, ast.Constant)
            ):
                bad["hardcoded_secret"].append(f"{p}:{node.lineno} {node.targets[0].id}")
    return bad


def compute_verdict(sections: list[Section]) -> str:
    """Verdict from section statuses: FAIL -> not ready; CONDITIONAL -> conditional."""
    if any(s.status == FAIL for s in sections):
        return "NOT PRODUCTION READY"
    if any(s.status == CONDITIONAL for s in sections):
        return "CONDITIONALLY PRODUCTION READY"
    return "PRODUCTION READY"


def section_security(port: int = 8905, pool: ServerPool | None = None, tmp: Path | None = None) -> Section:
    """Static src audit (deserialisation/eval/secrets) + live /metrics leak check."""
    sec = Section("security")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_sec_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        # ── Static scan of src (AST-based, no string greps) ─────────
        bad = audit_source_scan()
        if bad["pickle_load"]:
            return sec.fail(f"unsafe deserialisation: {bad['pickle_load'][:3]}")
        if bad["eval_exec"]:
            return sec.fail(f"eval/exec in source: {bad['eval_exec'][:3]}")
        if bad["hardcoded_secret"]:
            return sec.fail(f"hardcoded secrets: {bad['hardcoded_secret'][:3]}")
        sec.details["static_scan"] = {k: len(v) for k, v in bad.items()}

        # ── Live /metrics leak check: no filesystem paths, no secrets ──
        from benchmarks.common import write_csvs  # noqa: PLC0415

        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, 4, 200)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=1)
        err = pool.wait_ready(port)
        if err:
            return sec.fail(f"API did not become ready for security check: {err}")
        status, body = _request(port, "/metrics")
        if status != 200:
            return sec.fail(f"/metrics returned {status}")
        payload = json.loads(body)
        blob = json.dumps(payload)
        # json.dumps escapes backslashes, so a Windows path like
        # ``C:\Users`` renders as ``C:\\Users`` in the blob — check the
        # escaped form (and the raw body) so the marker is not a no-op.
        leaks = [
            marker for marker in (
                "data/raw", "C:\\", "history.json", "TELEGRAM_TOKEN", "password",
            )
            if marker in blob or marker in body
        ]
        if leaks:
            return sec.fail(f"/metrics exposed sensitive markers: {leaks}")
        sec.details["metrics_leak_check"] = "clean"
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)




def section_startup_deploy(tmp: Path | None = None) -> Section:
    """uvicorn/Streamlit clean startup (validated live elsewhere) + docker config."""
    sec = Section("startup_deploy")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_start_"))
    try:
        # Isolated env for the import smoke tests: app.py instantiates
        # the DataService and starts background refresh at import time,
        # so the child must run against a private DATA_DIRECTORY + NEPSE_HOME
        # and a private cwd or it would touch production state.
        env = dict(os.environ)
        env.update(_offline_provider_env())
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["DATA_DIRECTORY"] = str(tmp / "data")
        env["NEPSE_HOME"] = str(tmp / "nepse_home")

        # The import smoke tests run with ``cwd=tmp`` (WinError 267 if
        # the directory does not exist), and app.py instantiates the
        # DataService against DATA_DIRECTORY at import time — create both.
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "data").mkdir(parents=True, exist_ok=True)

        # Fresh-subprocess import of the FastAPI app (clean startup path).
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "import src.api.main"],
            cwd=str(tmp),
            env=env,
            capture_output=True,
            timeout=120,
            check=True,
        )
        api_import_ms = _fmt_ms(time.perf_counter() - start)

        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=str(tmp),
            env=env,
            capture_output=True,
            timeout=120,
            check=True,
        )
        streamlit_import_ms = _fmt_ms(time.perf_counter() - start)
        sec.details["api_import_ms"] = api_import_ms
        sec.details["app_import_ms"] = streamlit_import_ms

        # Docker compose config validation (no daemon required).
        import shutil  # noqa: PLC0415

        if shutil.which("docker") is None:
            sec.note("docker CLI unavailable — compose config not validated")
            sec.details["docker_compose_config"] = "SKIPPED (docker not found)"
            return sec.pass_()
        docker = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if docker.returncode != 0:
            return sec.conditional(
                "docker compose config failed",
                stderr=docker.stderr[:400] or f"exit {docker.returncode}",
            )
        sec.details["docker_compose_config"] = "valid"
        return sec.pass_()
    except subprocess.CalledProcessError as exc:
        stderr = getattr(exc, "stderr", None)
        detail = f"startup/deploy section raised: {exc}"
        if stderr:
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            detail += f"; stderr tail: {stderr[-400:]}"
        return sec.fail(detail)
    except (OSError, subprocess.SubprocessError) as exc:
        return sec.fail(f"startup/deploy section raised: {exc}")
    finally:
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def section_stability(
    minutes: int = 2,
    synthetic: int = 6,
    port: int = 8908,
    pool: ServerPool | None = None,
    tmp: Path | None = None,
) -> Section:
    """Long-running stability harness (opt-in, explicit duration)."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    sec = Section("stability")
    owned_tmp = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nepse_prod_stab_"))
    owned_pool = pool is None
    pool = pool or ServerPool()
    try:
        data_dir = tmp / "data"
        state_root = tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, synthetic, 300)
        if not _free_port(port):
            return sec.fail(f"port {port} already bound")
        pool.spawn(data_dir, state_root, port, workers=2)
        err = pool.wait_ready(port, timeout=120)
        if err:
            return sec.fail(f"stability API did not become ready: {err}")

        deadline = time.monotonic() + minutes * 60
        lat: list[float] = []
        statuses: list[int] = []
        mem_samples: list[float] = []
        endpoints = [f"/analyze/SYN{i:03d}" for i in range(synthetic)] + [
            "/portfolio", "/metrics", "/watchlist"
        ]
        idx = 0
        while time.monotonic() < deadline:
            path = endpoints[idx % len(endpoints)]
            idx += 1
            start = time.perf_counter()
            status, body = _request(port, path)
            lat.append((time.perf_counter() - start) * 1000.0)
            statuses.append(status)
            if path == "/metrics":
                try:
                    mem = (json.loads(body) or {}).get("memory_mb", 0.0)
                    mem_samples.append(float(mem or 0.0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            time.sleep(0.75)

        errors = len(statuses) - statuses.count(200)
        if errors:
            return sec.fail(
                f"stability run had {errors}/{len(statuses)} non-200 responses"
            )

        # Populate every measurement first, then decide the final
        # status — a ``conditional`` set here must be the *last* status
        # transition or the following ``pass_()`` would silently
        # downgrade it back to PASS (reviewer-confirmed evidence-loss
        # path: a memory-growth warning could never surface).
        sec.details["duration_min"] = minutes
        sec.details["requests"] = len(statuses)
        sec.details["errors"] = errors
        sec.details["latency"] = _percentiles(lat)
        sec.details["memory_samples"] = len(mem_samples)
        if mem_samples:
            sec.details["memory_first_mb"] = round(mem_samples[0], 1)
            sec.details["memory_last_mb"] = round(mem_samples[-1], 1)
        if len(mem_samples) >= 2 and mem_samples[-1] > mem_samples[0] * 1.5 + 50:
            return sec.conditional(
                "worker memory grew noticeably during the run",
                memory_first_mb=round(mem_samples[0], 1),
                memory_last_mb=round(mem_samples[-1], 1),
            )
        return sec.pass_()
    finally:
        _close_section(pool, owned_pool, port)
        if owned_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────
# Gate orchestration + report
# ───────────────────────────────────────────────────────────────────

def run_gate(
    synthetic: int = 12,
    real: int = 12,
    stability_min: int = 0,
    out_file: Path | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Run every section and return ``(production_ready, report)``.

    ``production_ready`` is True only when the verdict is not FAIL/CONDITIONAL
    (SKIPPED sections are documented, not hidden).
    """
    before = snapshot_state(PROJECT_ROOT)
    # Every subprocess (uvicorn workers, Streamlit, import smoke tests)
    # inherits os.environ, so point the providers at a dead port once,
    # up front, and every section stays hermetic and deterministic.
    # The original values are restored in ``finally`` so a caller that
    # runs the gate in-process (e.g. pytest) is not left with a mutated
    # environment afterwards.
    _env_backup = {
        k: os.environ.get(k) for k in _offline_provider_env()
    }
    os.environ.update(_offline_provider_env())
    pool = ServerPool()
    tmp = Path(tempfile.mkdtemp(prefix="nepse_prod_gate_"))
    sections: list[Section] = []
    try:
        # Dynamic port allocation: sections must never collide with each
        # other or with leftover servers from earlier runs.  The web
        # port is included so the Streamlit section is collision-free
        # too (its api + web ports come from the same pool).
        p_api, p_cache, p_mw, p_conc, p_web, p_api2, p_sec, p_rec, p_stab, p_bot = _free_ports(10)
        # Ordering is defensive, not load-bearing: the CPU-sensitive
        # performance section (ci_gate warm p99 tripwire, worst-of-60
        # per-symbol samples) runs before any server is spawned, and the
        # Sprint 13 per-section teardown fix (``_close_section`` in every
        # section's finally) guarantees a quiet machine for every
        # section regardless of order.  (Measured: warm p99 ~96 ms
        # standalone vs >2000 ms under 5 co-resident gate servers.)
        sections.append(section_core_pipeline(synthetic=synthetic, real=real, tmp=tmp / "core"))
        sections.append(section_persistence(tmp=tmp / "persist"))
        sections.append(section_performance(tmp=tmp / "perf"))
        sections.append(section_api_live(synthetic=synthetic, port=p_api, pool=pool, tmp=tmp / "api"))
        sections.append(section_cache(synthetic=synthetic, port=p_cache, pool=pool, tmp=tmp / "cache"))
        sections.append(section_multi_worker(synthetic=synthetic, port=p_mw, pool=pool, tmp=tmp / "mw"))
        sections.append(section_concurrency(synthetic=synthetic, port=p_conc, pool=pool, tmp=tmp / "conc"))
        sections.append(section_streamlit(api_port=p_api2, web_port=p_web, api_pool=pool, tmp=tmp / "stream"))
        sections.append(section_bot(synthetic=synthetic, port=p_bot, pool=pool, tmp=tmp / "bot"))
        sections.append(section_recovery(synthetic=synthetic, port=p_rec, pool=pool, tmp=tmp / "rec"))
        sections.append(section_security(port=p_sec, pool=pool, tmp=tmp / "sec"))
        sections.append(section_startup_deploy(tmp=tmp / "start"))
        if stability_min > 0:
            sections.append(
                section_stability(minutes=stability_min, synthetic=synthetic, port=p_stab, pool=pool, tmp=tmp / "stab")
            )

        after = snapshot_state(PROJECT_ROOT)
        state_ok = verify_state_unchanged(before, after)
        state_notes = _state_notes(before, after)

        failures = [s.name for s in sections if s.status == FAIL]
        conditionals = [s.name for s in sections if s.status == CONDITIONAL]
        skipped = [s.name for s in sections if s.status == SKIPPED]

        if not state_ok:
            # Production state pollution is a P0-class finding regardless
            # of section statuses.
            report: dict[str, Any] = {
                "verdict": "NOT PRODUCTION READY",
                "state_isolation": "FAILED",
                "state_notes": state_notes,
                "sections": [s.to_dict() for s in sections],
                "summary": {
                    "pass": len(sections) - len(failures) - len(conditionals) - len(skipped),
                    "fail": len(failures),
                    "conditional": len(conditionals),
                    "skipped": len(skipped),
                },
            }
            if out_file is not None:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return False, report

        verdict = compute_verdict(sections)

        report: dict[str, Any] = {
            "verdict": verdict,
            "state_isolation": "PASS" if state_ok else "FAILED",
            "sections": [s.to_dict() for s in sections],
            "summary": {
                "pass": len(sections) - len(failures) - len(conditionals) - len(skipped),
                "fail": len(failures),
                "conditional": len(conditionals),
                "skipped": len(skipped),
            },
            "skipped_reasons": {s.name: s.details.get("reason", "") for s in sections if s.status == SKIPPED},
        }
        if out_file is not None:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return verdict == "PRODUCTION READY", report
    finally:
        pool.shutdown_all()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        # Restore the caller's environment exactly as we found it.
        for k, v in _env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _print_report(report: dict[str, Any]) -> None:
    line = "=" * 60
    print(f"\n{line}")
    print("  NEPSE QUANT ENGINE — PRODUCTION READINESS GATE")
    print(f"{line}")
    print("  STATE ISOLATION  : " + report["state_isolation"])
    print()
    for sec in report["sections"]:
        label = STATUS_LABEL.get(sec["status"], sec["status"])
        print(f"  [{label}] {sec['name']}")
        for note in sec.get("notes", []):
            print(f"        note: {note}")
    print()
    print(f"  SUMMARY: {report['summary']['pass']} PASS | "
          f"{report['summary']['fail']} FAIL | "
          f"{report['summary']['conditional']} CONDITIONAL | "
          f"{report['summary']['skipped']} SKIPPED")
    if report.get("skipped_reasons"):
        for name, reason in report["skipped_reasons"].items():
            print(f"        skipped: {name} — {reason}")
    print()
    print(f"  FINAL RESULT: {report['verdict']}")
    print(line)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NEPSE production readiness gate (Sprint 13.0)"
    )
    parser.add_argument("--synthetic", type=int, default=12,
                        help="Synthetic symbols per section (default 12)")
    parser.add_argument("--real", type=int, default=12,
                        help="Real-corpus symbols for the core-pipeline leg (default 12)")
    parser.add_argument("--stability-min", type=int, default=0,
                        help="Optional long-running stability section duration in minutes")
    parser.add_argument("--out", type=str, default="",
                        help="Artifact JSON path (default benchmarks/results/production_gate.json)")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    out = Path(args.out) if args.out else (
        PROJECT_ROOT / "benchmarks" / "results" / "production_gate.json"
    )
    ready, report = run_gate(
        synthetic=args.synthetic,
        real=args.real,
        stability_min=args.stability_min,
        out_file=out,
    )
    _print_report(report)
    print(f"  artifact: {out.resolve()}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
