"""Sprint 12.3 — Warm pipeline profiling & cache effectiveness.

Covers:

- **Warm benchmark** (Phases 2/3): ``benchmarks.warm_path.bench_warm_scan``
  runs cold and warm ``scan_market`` over a deterministic corpus and
  reports observable counters — CSV reads, scanner/indicator cache
  hits/misses, alert reads/writes, failures.  Assertions are structural
  (``csv_reads == 0`` warm, hits > 0 warm, misses == 0 warm, alert I/O
  only on cold) and never wall-clock.
- **Cache behaviour** (Phase 3): expected hit on the same fingerprint,
  miss after the source file changes, worker/process scope documented
  (the caches are process-global singletons), and the Sprint 12.3
  production change — the scanner-cache LRU bound default (600) now
  covers the real corpus so a warm scan of the full corpus has zero CSV
  re-reads, whereas a *small* bound (as in the measured before-state)
  evicts beyond its capacity.
- **Indicator profiling** (Phase 5): ``profile_indicator_families``
  attributes one cold analysis to the four families (moving averages /
  momentum / volume / volatility); at least one family must have
  non-zero cumulative time (regression for the pstats module-string
  matching fix), and the warm re-analysis is a 100 % cache hit.
- **Warm profiler** (Phase 4): ``profile_warm_scan`` warms the cache,
  profiles a second scan and records warm-phase analysis hits/misses;
  the profiled run must be all hits (no misses), proving the warm path
  never re-runs the indicator chain.
- **State isolation**: benchmark/profiler runs under ``_state_files`` —
  the production ``data/alerts`` file set (name, size, mtime) is
  byte-identical before and after.

No exact runtime assertions anywhere — structure, determinism and
counters only.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.config import SCANNER_CACHE_MAX_ENTRIES


# ───────────────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────────────


def _alerts_file_set() -> set[tuple[str, int, int]]:
    """Snapshot of the production alert-history directory."""
    alerts_dir = Path("data/alerts")
    if not alerts_dir.exists():
        return set()
    return {
        (p.name, p.stat().st_size, p.stat().st_mtime_ns)
        for p in alerts_dir.iterdir()
        if p.is_file()
    }


def _write_csvs_and_scan(data_dir: Path, symbols: int, rows: int, **kw) -> dict:
    """Run the warm benchmark on a tiny synthetic corpus."""
    from benchmarks.warm_path import bench_warm_scan

    return bench_warm_scan(
        data_dir,
        symbols,
        rows,
        populate=True,
        cold_reps=kw.get("cold_reps", 2),
        warm_reps=kw.get("warm_reps", 2),
        workers=1,
        corpus="synthetic",
    )


# ───────────────────────────────────────────────────────────────────
# Phase 2/3 — warm benchmark behaviour + counters
# ───────────────────────────────────────────────────────────────────


class TestWarmBenchmark:
    def test_cold_and_warm_phases_execute(self, tmp_path):
        """Cold and warm phases both run the production scanner and
        report the documented structure (no wall-clock assertions)."""
        result = _write_csvs_and_scan(tmp_path, 4, 80)
        assert result["symbols"] == 4
        assert result["corpus"] == "synthetic"
        assert result["mode"] == "sequential"
        for phase in ("cold", "warm"):
            p = result[phase]
            assert set(p["latency"]) == {"best_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms"}
            assert p["per_symbol_ms"] >= 0
            assert p["csv_reads"] >= 0
            assert set(p["scanner"]) == {
                "df_hits", "df_misses", "analysis_hits", "analysis_misses", "hit_rate_pct",
            }
            assert set(p["indicator"]) == {"hits", "misses", "consulted", "hit_rate_pct"}
            assert p["alert_reads"] >= 0
            assert p["alert_writes"] >= 0
        assert result["failures"] == 0

    def test_warm_phase_is_cache_hit_only(self, tmp_path):
        """A warm scan must be pure cache hits: zero CSV reads, zero
        analysis misses, zero alert I/O, and the indicator cache never
        consulted (the scanner analysis cache answers first)."""
        result = _write_csvs_and_scan(tmp_path, 4, 80)
        warm = result["warm"]
        assert warm["csv_reads"] == 0
        assert warm["scanner"]["analysis_misses"] == 0
        assert warm["scanner"]["df_misses"] == 0
        assert warm["scanner"]["analysis_hits"] > 0
        assert warm["alert_reads"] == 0
        assert warm["alert_writes"] == 0
        assert warm["indicator"]["consulted"] is False

    def test_cold_phase_does_full_work(self, tmp_path):
        """Cold: every symbol is parsed + analysed + alert-batched once
        per repetition (per-phase totals, not last-rep)."""
        cold_reps, warm_reps = 2, 2
        result = _write_csvs_and_scan(
            tmp_path, 4, 80, cold_reps=cold_reps, warm_reps=warm_reps
        )
        cold = result["cold"]
        assert cold["csv_reads"] == 4 * cold_reps  # 4 symbols × reps
        assert cold["scanner"]["analysis_misses"] == 4 * cold_reps
        assert cold["scanner"]["analysis_hits"] == 0
        assert cold["indicator"]["consulted"] is True
        # One alert-history read + one write per scan (batch path).
        assert cold["alert_reads"] == cold_reps
        assert cold["alert_writes"] == cold_reps
        assert cold["indicator"]["misses"] == 4 * cold_reps

    def test_warm_speedup_is_reported(self, tmp_path):
        """speedup_x is present and positive; it is a ratio, so no
        wall-clock assertion is needed."""
        result = _write_csvs_and_scan(tmp_path, 3, 60)
        assert result["speedup_x"] > 0

    def test_deterministic_corpus(self, tmp_path):
        """The same seeded corpus produces identical cache counter
        behaviour across two runs (determinism of structure/counters)."""
        from benchmarks.common import write_csvs

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 3, 60)
        r1 = _write_csvs_and_scan(data_dir, 3, 60, populate=False)
        r2 = _write_csvs_and_scan(data_dir, 3, 60, populate=False)
        assert r1["cold"]["csv_reads"] == r2["cold"]["csv_reads"]
        assert r1["warm"]["csv_reads"] == r2["warm"]["csv_reads"] == 0
        assert r1["warm"]["scanner"]["analysis_hits"] == r2["warm"]["scanner"]["analysis_hits"]

    def test_production_state_untouched(self, tmp_path):
        """Benchmarking never mutates the production alert-history dir."""
        before = _alerts_file_set()
        _write_csvs_and_scan(tmp_path, 3, 60)
        assert _alerts_file_set() == before


# ───────────────────────────────────────────────────────────────────
# Phase 3 — cache behaviour + the Sprint 12.3 capacity change
# ───────────────────────────────────────────────────────────────────


class TestCacheBehaviour:
    def test_same_fingerprint_hits_miss_after_change(self, tmp_path):
        """Scanner cache: same file = hit; content change = miss
        (fingerprint embeds mtime/size).  Exercises the real scanner
        path — ``analyze_stock`` alone does not populate the scanner
        analysis tier (only ``scan_market`` attaches ``new_alerts`` and
        calls ``put_analysis``)."""
        from benchmarks.common import write_csvs
        from benchmarks.pipeline import _state_files
        from src.cache.scanner_cache import scanner_cache
        from src.scanner import engine as scanner_engine

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 1, 40)
        path = sorted(data_dir.glob("*.csv"))[0]

        old_dir = scanner_engine.DATA_DIRECTORY
        scanner_engine.DATA_DIRECTORY = str(data_dir)
        try:
            with _state_files(tmp_path):
                scanner_cache.clear()
                assert scanner_cache.get_analysis(path) is None
                scanner_engine.scan_market(workers=1)  # populates the tier
                assert scanner_cache.get_analysis(path) is not None

                # Changing content (mtime/size) invalidates the fingerprint.
                data = path.read_text(encoding="utf-8")
                path.write_text(data + "\n", encoding="utf-8")
                assert scanner_cache.get_analysis(path) is None
        finally:
            scanner_engine.DATA_DIRECTORY = old_dir
            scanner_cache.clear()

    def test_worker_scope_is_process_global(self):
        """The scanner + indicator caches are module-level singletons:
        importing them through *different* modules yields the same
        object, which is what lets scanner worker threads share one
        cache (documented scope, asserted by identity)."""
        from src.cache.scanner_cache import scanner_cache as sc1
        from src.indicators.cache import indicator_cache as ic1
        # Second import path — the scanner engine module binds the
        # singleton into its own namespace when it imports it.
        from src.scanner.engine import scanner_cache as sc2
        from src.engine.analyzer import indicator_cache as ic2

        assert sc1 is sc2
        assert ic1 is ic2

    def test_default_capacity_covers_real_corpus(self):
        """Sprint 12.3 production change: the scanner-cache LRU bound
        default (600) must cover the real corpus (~287 files) and the
        500-symbol scaling target, so a warm full-corpus scan has no LRU
        eviction.  The measured before-state (200) did not — warm-500
        re-read 1500 CSVs (60 % eviction)."""
        assert SCANNER_CACHE_MAX_ENTRIES >= 500
        # A small bound still evicts: the cache enforces max_entries per
        # tier regardless of the default.
        from benchmarks.common import write_csvs
        from src.cache.scanner_cache import ScannerCache

        small = ScannerCache(ttl=3600, max_entries=2)
        with tempfile.TemporaryDirectory(prefix="nepse_lru_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, 4, 40)
            for p in sorted(data_dir.glob("*.csv")):
                small.put_analysis(p, {"symbol": p.stem})
            assert small.stats()["analysis_entries"] == 2  # LRU capped

    def test_full_real_corpus_warm_scan_is_all_hits(self):
        """The real corpus fits in the raised default: a warm scan of
        the full usable corpus (minus the scanner-excluded ``sample``)
        must have zero CSV re-reads and pure analysis hits (this was
        the measured defect the capacity change fixes).  The expected
        hit count is derived from the *actual scanned* symbol count, not
        the audited selection, because ``scan_market`` excludes
        ``sample.csv`` while the audit's ``ok`` flag is only a warning."""
        from benchmarks.corpus import copy_corpus
        from benchmarks.warm_path import _select_usable_real_corpus, bench_warm_scan

        spec, excluded = _select_usable_real_corpus(500)  # clamps to all usable
        assert len(spec.symbols) >= 270  # the full real corpus fits
        assert excluded  # the seven too-few-rows files are recorded
        with tempfile.TemporaryDirectory(prefix="nepse_realwarm_") as tmp:
            data_dir = Path(tmp) / "data"
            copy_corpus(spec, data_dir)
            result = bench_warm_scan(
                data_dir,
                len(spec.symbols),
                0,
                populate=False,
                cold_reps=1,
                warm_reps=2,
                workers=1,
                corpus="real",
            )
            scanned = len(list(data_dir.glob("*.csv")))
            assert result["symbols"] == scanned
            assert result["warm"]["csv_reads"] == 0
            assert result["warm"]["scanner"]["analysis_misses"] == 0
            assert result["warm"]["scanner"]["analysis_hits"] == 2 * scanned


# ───────────────────────────────────────────────────────────────────
# Phase 4/5 — warm profiler + per-family indicator attribution
# ───────────────────────────────────────────────────────────────────


class TestWarmProfiling:
    def test_profile_warm_scan_is_all_hits(self, tmp_path):
        """The profiled warm scan must be pure cache hits — the warm
        path never re-runs the indicator chain (no misses)."""
        from benchmarks.common import write_csvs
        from benchmarks.profile_warm_path import profile_warm_scan

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 4, 80)
        summary = profile_warm_scan(data_dir)
        assert summary["total_s"] >= 0
        assert summary["top"]
        assert summary["warm_phase_analysis_hits"] == 4
        assert summary["warm_phase_analysis_misses"] == 0

    def test_profile_warm_scan_isolates_state(self, tmp_path):
        from benchmarks.common import write_csvs
        from benchmarks.profile_warm_path import profile_warm_scan

        before = _alerts_file_set()
        data_dir = tmp_path / "data"
        write_csvs(data_dir, 3, 60)
        profile_warm_scan(data_dir)
        assert _alerts_file_set() == before

    def test_indicator_families_discovered(self):
        """``profile_indicator_families`` attributes the cold chain to
        the four families; at least one family must have measurable
        cumulative time (regression for the pstats module-string
        matching fix — dotted names never matched file-path modules)."""
        from benchmarks.profile_warm_path import profile_indicator_families

        profile = profile_indicator_families(rows=200)
        assert set(profile["families"]) == {
            "moving_averages", "momentum", "volume", "volatility",
        }
        assert sum(
            f["cumtime_s"] for f in profile["families"].values()
        ) > 0  # the chain actually ran and was attributed
        assert profile["warm_hit_rate"] == 1.0  # warm re-analysis = 100 % hit

    def test_run_warm_profiling_isolates_and_writes_artifacts(self, tmp_path):
        """``run_warm_profiling`` (tiny sizes) writes JSON + dumps +
        reports and never touches production state."""
        from benchmarks.profile_warm_path import run_warm_profiling

        before = _alerts_file_set()
        out = tmp_path / "profiles"
        results = run_warm_profiling(sizes=(2, 3), rows=60, out_dir=out)
        assert results["single"]["rows"] == 60
        assert len(results["sizes"]) == 2
        for size_result in results["sizes"]:
            assert size_result["symbols"] in (2, 3)
            assert Path(size_result["dump_path"]).exists()
            assert Path(size_result["report_path"]).exists()
        assert set(results["indicator_families"]["families"]) == {
            "moving_averages", "momentum", "volume", "volatility",
        }
        assert (out / "profile_warm_summary.json").exists()
        assert _alerts_file_set() == before


# ───────────────────────────────────────────────────────────────────
# Phase 6 — DataFrame copy audit (static)
# ───────────────────────────────────────────────────────────────────


class TestDataFrameCopyAudit:
    def test_copy_audit_structure(self):
        """The static copy audit reports per-file call-site counts."""
        from benchmarks.warm_path import audit_dataframe_copies

        audit = audit_dataframe_copies()
        files = audit["files"]
        assert "src/engine/analyzer.py" in files  # the 1 defensive copy
        assert "src/indicators/cache.py" in files  # copy-on-return sites
        for rel, info in files.items():
            assert info["count"] == len(info["copy_sites"])

    def test_copy_audit_deterministic(self):
        from benchmarks.warm_path import audit_dataframe_copies

        assert audit_dataframe_copies() == audit_dataframe_copies()

    def test_copy_audit_counts_only_code_calls(self, tmp_path, monkeypatch):
        """Regression: the audit counts real ``.copy(...)`` *call* nodes
        only — a docstring that merely *mentions* ``.copy()`` must not
        be counted.  (The pre-AST line-grep over-counted the
        ``copy semantics`` docstring bullet in ``indicators/cache.py``.)"""
        from benchmarks import warm_path

        # Fixture lines: 1 docstring open (mentions ``df.copy()``),
        # 2 docstring close, 3 blank, 4 ``def analyze``, 5 the real call.
        # Built with explicit lines so the embedded ``"""`` tokens can
        # never terminate the enclosing string literal.
        src = tmp_path / "pseudo.py"
        src.write_text(
            '"""Module docstring: ``df.copy()`` is mentioned here,\n'
            'but must not count."""\n'
            "\n"
            "def analyze(df):\n"
            "    out = df.copy()  # the one real call\n"
            '    out["x"] = 1\n'
            "    return out\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(warm_path, "PIPELINE_SOURCE_FILES", ("pseudo.py",))
        monkeypatch.setattr(warm_path, "PROJECT_ROOT", tmp_path)

        audit = warm_path.audit_dataframe_copies()
        info = audit["files"]["pseudo.py"]
        assert info["count"] == 1  # docstring mention excluded
        # ``out = df.copy()`` is line 5 of the fixture (1: docstring open,
        # 2: docstring close, 4: ``def analyze``).
        assert info["copy_sites"] == [5]
        # ``monkeypatch`` auto-restores PROJECT_ROOT + PIPELINE_SOURCE_FILES.

    def test_copy_audit_pins_exact_pipeline_counts(self):
        """Exact copy-site counts for the five pipeline files — pins the
        docstring-false-positive fix for ``indicators/cache.py`` (2 code
        sites, not the 3 a line-grep would report)."""
        from benchmarks.warm_path import audit_dataframe_copies

        audit = audit_dataframe_copies()
        expected = {
            "src/engine/analyzer.py": 1,
            "src/loaders/csv_loader.py": 1,
            "src/cache/scanner_cache.py": 1,
            "src/indicators/cache.py": 2,
            "src/backtest/engine.py": 1,
        }
        for rel, count in expected.items():
            assert audit["files"][rel]["count"] == count, rel
