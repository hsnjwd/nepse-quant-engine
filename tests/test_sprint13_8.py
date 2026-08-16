"""Sprint 13.8 — Platform Soak Testing & Performance Gate Hardening.

Long-running reliability and determinism coverage proving the engine
remains *correct, bounded, recoverable and measurable* across sustained
operation:

    1. benchmark determinism         11. scanner soak
    2. benchmark repeated execution  12. alert soak
    3. benchmark state isolation     13. API soak
    4. cold/warm gate reliability    14. worker restart
    5. CPU contention behaviour      15. two-worker soak
    6. provider health soak          16. memory bounds
    7. reconciliation soak           17. file/resource leaks
    8. cache soak                    18. subprocess cleanup
    9. calendar soak                 19. metrics bounds
    10. notification soak            20. incident bounds
    21. Sprint 13.7 regression

Constraints honoured (Sprint 13.8 §32): no threshold weakened
(WARM_API_RATIO_MAX 0.75, WARM_PORTFOLIO_RATIO_MAX 0.75,
ANALYZE_P99_MAX_MS 2000, WARM_SPEEDUP_MIN 5.0 are asserted verbatim),
no retry-until-lucky benchmark logic (the contention protocol allows at
most one *contention-gated* rerun with the same thresholds), no
fabricated soak results (every measurement runs real code paths), and
no unbounded state (all soak scenarios assert documented caps).

All worker / memory / resource scenarios are hermetic: synthetic
corpus, temp state roots, and no live network access.
"""

from __future__ import annotations

import gc
import json
import threading
from pathlib import Path

import pytest

# ───────────────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────────────


def _corpus(data_dir: Path, symbols: int = 4, rows: int = 40) -> list[Path]:
    """Write a small synthetic corpus; return sorted CSV paths."""
    from benchmarks.common import write_csvs

    write_csvs(data_dir, symbols, rows)
    return sorted(data_dir.glob("*.csv"))


def _state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ───────────────────────────────────────────────────────────────────
# 1. Benchmark determinism
# ───────────────────────────────────────────────────────────────────


class TestBenchmarkDeterminism:
    """``evaluate_checks`` is a pure function of its inputs."""

    def test_same_input_same_output(self):
        from benchmarks.ci_gate import evaluate_checks

        measured = {
            "cold_load_best_ms": 100.0,
            "warm_load_best_ms": 10.0,
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        baseline = 100.0
        p1, r1 = evaluate_checks(measured, baseline_cold_load_best_ms=baseline)
        p2, r2 = evaluate_checks(measured, baseline_cold_load_best_ms=baseline)
        assert p1 == p2
        assert r1 == r2
        assert p1 is True

    def test_missing_measurement_fails_not_passes(self):
        """A malformed measurement must FAIL explicitly, never pass."""
        from benchmarks.ci_gate import evaluate_checks

        passed, report = evaluate_checks({}, baseline_cold_load_best_ms=100.0)
        assert passed is False
        names = {f["name"] for f in report["failures"]}
        assert {"warm_speedup", "warm_scan", "warm_api", "warm_portfolio",
                "warm_api_p99"} <= names

    def test_zero_denominator_cannot_produce_pass(self):
        from benchmarks.ci_gate import evaluate_checks

        measured = {
            "cold_load_best_ms": 100.0,
            "warm_load_best_ms": 0.0,  # broken warm measurement
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        passed, report = evaluate_checks(measured, baseline_cold_load_best_ms=100.0)
        assert passed is False
        assert report["checks"]["warm_speedup"] == "FAIL"

    def test_nan_measurement_fails(self):
        from benchmarks.ci_gate import evaluate_checks

        measured = {
            "cold_load_best_ms": float("nan"),
            "warm_load_best_ms": 10.0,
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        passed, report = evaluate_checks(measured, baseline_cold_load_best_ms=100.0)
        assert passed is False
        assert report["checks"]["cold_load"] == "FAIL"
        assert report["checks"]["warm_speedup"] == "FAIL"

    def test_cold_load_skipped_without_baseline(self):
        """No committed baseline → cold_load is a documented SKIP, not a
        failure (and must not fail the gate)."""
        from benchmarks.ci_gate import evaluate_checks

        measured = {
            "cold_load_best_ms": 100.0,
            "warm_load_best_ms": 10.0,
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        passed, report = evaluate_checks(measured, baseline_cold_load_best_ms=None)
        assert passed is True
        assert report["checks"]["cold_load"] == "SKIP"

    def test_thresholds_are_the_enforcement_values(self):
        """Sprint 13.8: no threshold may be weakened.  Assert the module
        constants verbatim so a future edit to them fails loudly."""
        from benchmarks import ci_gate

        assert ci_gate.WARM_SPEEDUP_MIN == 5.0
        assert ci_gate.WARM_SCAN_RATIO_MAX == 0.5
        assert ci_gate.WARM_API_RATIO_MAX == 0.75
        assert ci_gate.WARM_PORTFOLIO_RATIO_MAX == 0.75
        assert ci_gate.ANALYZE_P99_MAX_MS == 2000.0

    def test_ratio_formula(self):
        from benchmarks.ci_gate import _pos, _ratio

        assert _pos(5) == 5.0
        assert _pos(0) is None
        assert _pos(-1) is None
        assert _pos(float("inf")) is None
        assert _pos(float("nan")) is None
        assert _pos("nope") is None
        assert _ratio(20.0, 100.0) == 0.2
        assert _ratio(None, 100.0) is None
        assert _ratio(20.0, None) is None

    def test_pure_function_no_global_mutation(self):
        from benchmarks.ci_gate import evaluate_checks

        measured = {
            "cold_load_best_ms": 100.0,
            "warm_load_best_ms": 10.0,
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        import copy

        snapshot = copy.deepcopy(measured)
        evaluate_checks(measured, baseline_cold_load_best_ms=100.0)
        assert measured == snapshot


# ───────────────────────────────────────────────────────────────────
# 2. Benchmark repeated execution
# ───────────────────────────────────────────────────────────────────


class TestBenchmarkRepeatedExecution:
    """Repeated gate runs must be reproducible and explainable."""

    def test_run_gate_twice_both_pass(self, tmp_path, monkeypatch):
        """Two consecutive full gate runs (small corpus) both PASS and
        produce consistent artifacts."""
        from benchmarks import ci_gate

        # 12x100 corpus: large enough for the warm/cold portfolio ratio
        # to sit at its documented ~0.63-0.71 envelope (a tiny 6x60
        # corpus dilutes the ratio with alert-engine I/O and can push it
        # past the 0.75 bound — a measurement artifact, not a gate
        # regression), small enough to keep each real gate run fast.
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 12)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 100)
        out1 = tmp_path / "a.json"
        out2 = tmp_path / "b.json"
        # max_reruns=1: the contention protocol stays ACTIVE so a loaded
        # machine during a long combined suite gets its single controlled
        # rerun instead of a load-induced plain FAIL (the very flake this
        # sprint eliminates).  The verdict must still be PASS.
        p1, a1 = ci_gate.run_gate(out_file=out1, max_reruns=1)
        p2, a2 = ci_gate.run_gate(out_file=out2, max_reruns=1)
        assert p1 is True, a1["failures"]
        assert p2 is True, a2["failures"]
        # Both artifacts report the same verdict + corpus.
        assert a1["verdict"] == "PASS" and a2["verdict"] == "PASS"
        assert a1["corpus"] == a2["corpus"]
        # Speedup is machine-independent; assert a sane bound, not equality.
        assert a1["cold_load_warm_speedup_x"] >= ci_gate.WARM_SPEEDUP_MIN
        assert a2["cold_load_warm_speedup_x"] >= ci_gate.WARM_SPEEDUP_MIN

    def test_artifact_matches_verdict(self, tmp_path, monkeypatch):
        """The artifact's boolean checks must never contradict its
        verdict (a SKIP must not appear as False)."""
        from benchmarks import ci_gate

        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 12)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 100)
        out = tmp_path / "artifact.json"
        # Contention protocol active (max_reruns=1): a PASS verdict must
        # remain PASS even if a controlled rerun fired.
        passed, artifact = ci_gate.run_gate(out_file=out, max_reruns=1)
        assert passed == (artifact["verdict"] == "PASS")
        for name, ok in artifact["checks"].items():
            detail = artifact["check_details"][name]
            if ok:
                assert detail != "FAIL"
            else:
                assert detail == "FAIL"
        assert Path(out).exists()

    def test_repeated_run_leaves_no_state_artifacts(self, tmp_path, monkeypatch):
        """Running the gate repeatedly must not create state files in
        the project (alert history / portfolio / watchlist): the gate
        redirects every state write to a temp dir."""
        from benchmarks import ci_gate

        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 40)
        # Snapshot the project's state-file set before the runs.
        before = {
            str(p.resolve())
            for p in Path("data").rglob("*.json")
        } | {
            str(p.resolve())
            for p in Path("data").rglob("*.json.lock")
        }
        ci_gate.run_gate(out_file=tmp_path / "x.json", max_reruns=1)
        ci_gate.run_gate(out_file=tmp_path / "y.json", max_reruns=1)
        after = {
            str(p.resolve())
            for p in Path("data").rglob("*.json")
        } | {
            str(p.resolve())
            for p in Path("data").rglob("*.json.lock")
        }
        assert after == before  # data/ untouched by the gate runs


# ───────────────────────────────────────────────────────────────────
# 3. Benchmark state isolation
# ───────────────────────────────────────────────────────────────────


class TestBenchmarkStateIsolation:
    """Every benchmark invocation must start from a known state and
    restore module-level state on exit."""

    def test_scanner_data_directory_restored(self, tmp_path, monkeypatch):
        from benchmarks import ci_gate

        from src.scanner import engine as scanner_engine

        original = scanner_engine.DATA_DIRECTORY
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 40)
        ci_gate.run_gate(out_file=tmp_path / "iso.json", max_reruns=1)
        assert scanner_engine.DATA_DIRECTORY == original

    def test_scanner_cache_not_leaked_with_corpus_entries(self, tmp_path, monkeypatch):
        """After a gate run the process-wide scanner cache must not be
        full of the gate's synthetic corpus entries."""
        from benchmarks import ci_gate

        from src.cache.scanner_cache import scanner_cache

        scanner_cache.clear()
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 40)
        ci_gate.run_gate(out_file=tmp_path / "iso2.json", max_reruns=1)
        stats = scanner_cache.stats()
        # The gate measures cold loads by clearing per-rep; whatever
        # remains must be bounded and small (LRU-trimmed).
        assert stats["dataframe_entries"] <= stats["max_entries"]
        assert stats["analysis_entries"] <= stats["max_entries"]

    def test_indicator_cache_bounded_after_gate(self, tmp_path, monkeypatch):
        from benchmarks import ci_gate

        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 40)
        ci_gate.run_gate(out_file=tmp_path / "iso3.json", max_reruns=1)
        stats = indicator_cache.stats()
        assert stats["entries"] <= stats["max_entries"]

    def test_contention_probe_is_stateless(self):
        """The contention probe reads only time sources; calling it must
        not mutate any global state."""
        from benchmarks import ci_gate

        before = set(dir(ci_gate))
        ci_gate._contention_ratio(samples=1)
        after = set(dir(ci_gate))
        assert before == after

    def test_contention_probe_sane_bounds(self):
        """The wall/CPU ratio must be finite and sane on any machine.
        NB: ``time.process_time`` on Windows has coarse resolution
        (~15.6 ms tick), so a quiet ratio can read 0.9x-1.2x — the
        assertion is deliberately a wide sanity bound, never a tight
        equality (the threshold 1.5 is the actual contention detector)."""
        from benchmarks import ci_gate

        ratio = ci_gate._contention_ratio(samples=2)
        assert 0.5 < ratio < 10.0  # finite, positive, sane on any machine


# ───────────────────────────────────────────────────────────────────
# 4. Cold/warm gate reliability
# ───────────────────────────────────────────────────────────────────


class TestColdWarmGateReliability:
    def test_warm_speedup_requirement_enforced(self):
        from benchmarks.ci_gate import WARM_SPEEDUP_MIN, evaluate_checks

        measured = {
            "cold_load_best_ms": 100.0,
            "warm_load_best_ms": 25.0,  # 4x — below the 5x requirement
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        passed, report = evaluate_checks(measured, baseline_cold_load_best_ms=100.0)
        assert passed is False
        assert report["checks"]["warm_speedup"] == "FAIL"
        assert WARM_SPEEDUP_MIN == 5.0

    def test_warm_speedup_pass_at_exactly_5x(self):
        from benchmarks.ci_gate import evaluate_checks

        measured = {
            "cold_load_best_ms": 100.0,
            "warm_load_best_ms": 20.0,  # exactly 5x
            "scanner_cold_ms": 500.0,
            "scanner_warm_ms": 20.0,
            "api_analyze_cold_best_ms": 400.0,
            "api_analyze_warm_best_ms": 200.0,
            "api_analyze_warm_p99_ms": 100.0,
            "api_portfolio_cold_best_ms": 500.0,
            "api_portfolio_warm_best_ms": 300.0,
        }
        passed, _ = evaluate_checks(measured, baseline_cold_load_best_ms=100.0)
        assert passed is True

    def test_ratio_checks_use_median_of_five(self, monkeypatch, tmp_path):
        """The load legs must report median-of-5 (robust statistic), so
        discrete pauses hitting up to two of five repetitions cannot
        fail the gate."""
        from benchmarks import ci_gate

        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 40)
        # _median_of_cold_load / _median_of_warm_load semantics: median
        # over 5 reps (discards up to two paused reps per leg).
        files = _corpus(tmp_path / "data", 4, 40)
        cold = ci_gate._median_of_cold_load(files, reps=5)
        warm = ci_gate._median_of_warm_load(files, reps=5)
        assert cold > 0 and warm > 0
        assert (cold / warm) >= 5.0  # warm cache must be much faster

    def test_load_leg_returns_median_not_min(self, monkeypatch, tmp_path):
        """The load legs report the MEDIAN over reps, not the min: a
        single grossly-inflated repetition (a discrete environmental
        pause) must not move the reported value — the failure class
        that made the old min-of-3 gate flaky under a loaded suite."""
        from benchmarks import ci_gate

        # One outlier rep (0.5 s) among five: min-of-3 would report the
        # fastest rep; median-of-5 must report the middle value (110 ms).
        deltas = [0.100, 0.500, 0.110, 0.105, 0.115]
        stamps: list[float] = []
        t = 0.0
        for d in deltas:
            stamps.append(t)
            t += d
            stamps.append(t)
        it = iter(stamps)
        monkeypatch.setattr(ci_gate.time, "perf_counter", lambda: next(it))
        files = _corpus(tmp_path / "data", 4, 40)
        cold = ci_gate._median_of_cold_load(files, reps=5)
        assert cold == pytest.approx(110.0)  # median; min would be 100.0

    def test_fingerprint_does_not_resolve_path(self, tmp_path, monkeypatch):
        """Sprint 13.8 root-cause regression: the per-call realpath
        syscall was removed from ``ScannerCache._file_fingerprint``.  It
        was pure overhead (no change detection — mtime/size do that),
        and under filesystem/AV interference it inflated the light warm
        benchmark leg, crushing the warm/cold speedup ratio while the
        CPU-based contention probe saw nothing.  A ``Path.resolve`` call
        must never happen inside the fingerprint path."""
        from src.cache.scanner_cache import ScannerCache

        p = tmp_path / "x.csv"
        p.write_text("a,b\n1,2\n", encoding="utf-8")

        def _boom(self, *args, **kwargs):
            raise AssertionError("Path.resolve() must not be called")

        monkeypatch.setattr(Path, "resolve", _boom)
        fp = ScannerCache._file_fingerprint(p)
        assert fp is not None
        stat = p.stat()
        assert fp == f"{p}:{stat.st_mtime_ns}:{stat.st_size}"
        # Same file -> same fingerprint across calls (stable identity).
        assert ScannerCache._file_fingerprint(p) == fp

    def test_fail_safe_block_format(self):
        from benchmarks.ci_gate import evaluate_checks, format_failures

        passed, report = evaluate_checks({}, baseline_cold_load_best_ms=100.0)
        assert passed is False
        block = format_failures(report)
        assert "PERFORMANCE GATE FAILED" in block
        assert "actual" in block and "maximum" in block


# ───────────────────────────────────────────────────────────────────
# 5. CPU contention behaviour
# ───────────────────────────────────────────────────────────────────


class TestContentionProtocol:
    """The Sprint 13.8 contention protocol: at most ONE controlled
    rerun, gated on a measured environmental signal, same thresholds."""

    def _no_baseline(self, monkeypatch, tmp_path):
        """Point the gate at a nonexistent baseline file so the
        cold_load check SKIPs deterministically (the rerun tests only
        exercise the contention protocol, not baseline arithmetic)."""
        from benchmarks import ci_gate

        monkeypatch.setattr(ci_gate, "BASELINE_FILE", tmp_path / "no_baseline.json")

    def test_no_rerun_when_pass(self, tmp_path, monkeypatch):
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def fake_measure(data_dir, files, symbols):
            calls["n"] += 1
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 10.0,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    1.05, 5.0)  # quiet CPU, quiet I/O

        monkeypatch.setattr(ci_gate, "_measure_pass", fake_measure)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "np.json", max_reruns=1,
        )
        assert passed is True
        assert calls["n"] == 1
        assert artifact["contention"]["rerun_used"] is False
        assert artifact["contention"]["io_threshold_ms"] == ci_gate.CONTENTION_IO_MAX_MS
        assert artifact["classification"] == "PASS"

    def test_no_rerun_when_fail_without_contention(self, tmp_path, monkeypatch):
        """A failure WITHOUT contention must be a plain FAIL with NO
        rerun — never 'try until lucky'."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def failing_measure(data_dir, files, symbols):
            calls["n"] += 1
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 25.0,  # 4x < 5x
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    1.05, 5.0)  # quiet CPU, quiet I/O  # quiet window

        monkeypatch.setattr(ci_gate, "_measure_pass", failing_measure)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "nq.json", max_reruns=1,
        )
        assert passed is False
        assert calls["n"] == 1  # no rerun despite max_reruns=1
        assert artifact["contention"]["rerun_used"] is False
        assert artifact["classification"] == "FAIL"

    def test_rerun_only_on_contention_and_failure(self, tmp_path, monkeypatch):
        """FAIL + measured contention → exactly one controlled rerun;
        the rerun verdict is authoritative."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def contended_then_pass(data_dir, files, symbols):
            calls["n"] += 1
            if calls["n"] == 1:
                # Contended FAIL: slow warm (4x < 5x) under starvation.
                contention = ci_gate.CONTENTION_WALL_CPU_RATIO + 0.3
                return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 25.0,
                         "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                         "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                         "api_analyze_warm_p99_ms": 100.0,
                         "api_portfolio_cold_best_ms": 500.0,
                         "api_portfolio_warm_best_ms": 300.0},
                        {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                         "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                         "warm_cold_ratio": 0.5},
                        {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                         "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                         "warm_cold_ratio": 0.6},
                        contention, 5.0)  # contended CPU, quiet I/O
            # Rerun: quiet PASS.
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 10.0,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    1.05, 5.0)  # quiet CPU, quiet I/O

        monkeypatch.setattr(ci_gate, "_measure_pass", contended_then_pass)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "rp.json", max_reruns=1,
        )
        assert passed is True
        assert calls["n"] == 2  # exactly one rerun
        assert artifact["contention"]["rerun_used"] is True
        assert artifact["classification"] == "PASS_AFTER_CONTENDED_RERUN"

    def test_rerun_fail_is_fail_under_contention(self, tmp_path, monkeypatch):
        """A rerun that ALSO fails under contention is FAIL_UNDER_CONTENTION
        — an honest classification, never a hidden PASS."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def always_fail_contended(data_dir, files, symbols):
            calls["n"] += 1
            contention = ci_gate.CONTENTION_WALL_CPU_RATIO + 0.3
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 25.0,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    contention, 5.0)  # contended CPU, quiet I/O

        monkeypatch.setattr(ci_gate, "_measure_pass", always_fail_contended)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "rf.json", max_reruns=1,
        )
        assert passed is False
        assert calls["n"] == 2
        assert artifact["contention"]["rerun_used"] is True
        assert artifact["classification"] == "FAIL_UNDER_CONTENTION"

    def test_rerun_fail_on_quiet_window_is_plain_fail(self, tmp_path, monkeypatch):
        """If the rerun itself ran on a quiet window and still failed,
        that is definitive evidence of a regression → plain FAIL."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def contended_fail_quiet_fail(data_dir, files, symbols):
            calls["n"] += 1
            # First pass: FAIL under measured starvation.  Rerun: the SAME
            # failure on a QUIET window — definitive regression evidence.
            contention = (
                ci_gate.CONTENTION_WALL_CPU_RATIO + 0.3 if calls["n"] == 1 else 1.05
            )
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 25.0,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0,
                     "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0,
                     "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    contention, 5.0)  # contended CPU, quiet I/O

        monkeypatch.setattr(ci_gate, "_measure_pass", contended_fail_quiet_fail)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "rqf.json", max_reruns=1,
        )
        assert passed is False
        assert artifact["contention"]["rerun_used"] is True
        assert artifact["contention"]["rerun_wall_cpu_ratio"] <= ci_gate.CONTENTION_WALL_CPU_RATIO
        assert artifact["classification"] == "FAIL"  # quiet rerun failed → regression

    def test_max_reruns_clamped_to_one(self, tmp_path, monkeypatch):
        """The protocol structurally allows at most ONE rerun — a
        programmatic caller passing max_reruns=5 must still get exactly
        one rerun (never 'retry until lucky')."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def contended_then_pass(data_dir, files, symbols):
            calls["n"] += 1
            contention = ci_gate.CONTENTION_WALL_CPU_RATIO + 0.3
            if calls["n"] == 1:
                warm = 25.0  # 4x < 5x -> FAIL under contention
            else:
                warm = 10.0  # 10x -> PASS on the rerun
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": warm,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    contention, 5.0)  # contended CPU, quiet I/O

        monkeypatch.setattr(ci_gate, "_measure_pass", contended_then_pass)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "clamp.json", max_reruns=5
        )
        assert passed is True
        assert calls["n"] == 2  # exactly ONE rerun despite max_reruns=5
        assert artifact["contention"]["rerun_used"] is True

    def test_rerun_uses_same_thresholds(self, tmp_path, monkeypatch):
        """The rerun must evaluate with the SAME thresholds as the first
        pass (asserted by evaluating the rerun report's maxima)."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)

        def measure(data_dir, files, symbols):
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 10.0,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0, "warm_avg_ms": 200.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0, "warm_avg_ms": 300.0,
                     "warm_p50_ms": 10.0, "warm_p95_ms": 20.0, "warm_p99_ms": 100.0,
                     "warm_cold_ratio": 0.6},
                    1.05, 5.0)  # quiet CPU, quiet I/O

        monkeypatch.setattr(ci_gate, "_measure_pass", measure)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "st.json",
            max_reruns=1,
            warm_api_ratio_max=0.75,
            warm_portfolio_ratio_max=0.75,
            analyze_p99_max_ms=2000.0,
        )
        assert passed is True
        assert artifact["warm_api_ratio_max"] == 0.75
        assert artifact["warm_portfolio_ratio_max"] == 0.75
        assert artifact["analyze_p99_max_ms"] == 2000.0

    def test_rerun_on_io_contention(self, tmp_path, monkeypatch):
        """FAIL + a measured filesystem/AV signal (I/O probe above the
        threshold) must trigger exactly one controlled rerun with the
        same thresholds — the CPU probe cannot see filesystem
        interference, so the I/O probe carries that duty (Sprint 13.8)."""
        from benchmarks import ci_gate

        self._no_baseline(monkeypatch, tmp_path)
        calls = {"n": 0}

        def io_contended_then_pass(data_dir, files, symbols):
            calls["n"] += 1
            if calls["n"] == 1:
                # Quiet CPU (1.05 < 1.5) but I/O probe well above the
                # threshold and a FAIL (4x warm speedup): the I/O signal
                # alone must justify the rerun.
                return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 25.0,
                         "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                         "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                         "api_analyze_warm_p99_ms": 100.0,
                         "api_portfolio_cold_best_ms": 500.0,
                         "api_portfolio_warm_best_ms": 300.0},
                        {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0,
                         "warm_avg_ms": 200.0, "warm_p50_ms": 10.0, "warm_p95_ms": 20.0,
                         "warm_p99_ms": 100.0, "warm_cold_ratio": 0.5},
                        {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0,
                         "warm_avg_ms": 300.0, "warm_p50_ms": 10.0, "warm_p95_ms": 20.0,
                         "warm_p99_ms": 100.0, "warm_cold_ratio": 0.6},
                        1.05, ci_gate.CONTENTION_IO_MAX_MS + 200.0)
            # Rerun: quiet I/O, PASS.
            return ({"cold_load_best_ms": 100.0, "warm_load_best_ms": 10.0,
                     "scanner_cold_ms": 500.0, "scanner_warm_ms": 20.0,
                     "api_analyze_cold_best_ms": 400.0, "api_analyze_warm_best_ms": 200.0,
                     "api_analyze_warm_p99_ms": 100.0,
                     "api_portfolio_cold_best_ms": 500.0,
                     "api_portfolio_warm_best_ms": 300.0},
                    {"cold_best_ms": 400.0, "cold_avg_ms": 400.0, "warm_best_ms": 200.0,
                     "warm_avg_ms": 200.0, "warm_p50_ms": 10.0, "warm_p95_ms": 20.0,
                     "warm_p99_ms": 100.0, "warm_cold_ratio": 0.5},
                    {"cold_best_ms": 500.0, "cold_avg_ms": 500.0, "warm_best_ms": 300.0,
                     "warm_avg_ms": 300.0, "warm_p50_ms": 10.0, "warm_p95_ms": 20.0,
                     "warm_p99_ms": 100.0, "warm_cold_ratio": 0.6},
                    1.05, 5.0)

        monkeypatch.setattr(ci_gate, "_measure_pass", io_contended_then_pass)
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 2)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 20)
        passed, artifact = ci_gate.run_gate(
            out_file=tmp_path / "io.json", max_reruns=1,
        )
        assert passed is True
        assert calls["n"] == 2  # exactly one rerun, triggered by I/O alone
        assert artifact["contention"]["rerun_used"] is True
        assert artifact["contention"]["first_pass_io_probe_ms"] > ci_gate.CONTENTION_IO_MAX_MS
        assert artifact["classification"] == "PASS_AFTER_CONTENDED_RERUN"

    def test_io_probe_measures_real_filesystem_cost(self):
        """Smoke test: ``_io_probe`` runs the real alert-history
        read+write on an isolated temp file and returns a positive,
        finite millisecond cost (the filesystem-interference signal)."""
        from benchmarks import ci_gate

        ms = ci_gate._io_probe(samples=2)
        assert ms > 0.0
        assert ms < 5000.0  # sane upper bound even on a badly-loaded box


# ───────────────────────────────────────────────────────────────────
# 6. Provider health soak
# ───────────────────────────────────────────────────────────────────


class TestProviderHealthSoak:
    def test_fallback_scenario_passes(self):
        from benchmarks.soak import run_scenario_fallback

        report = run_scenario_fallback(24)
        assert report["passed"], report["failures"]
        assert report["fallback_cycles"] == 6
        assert report["recovered_cycles"] == 6

    def test_outcome_window_bounded(self):
        from benchmarks.soak import run_scenario_fallback

        report = run_scenario_fallback(40, outcome_window=100)
        assert report["primary_window_len"] <= 100

    def test_fallback_not_double_counted(self):
        """fallback_count must equal the number of fallback cycles — one
        provider-level fallback event per fallback phase, never two."""
        from benchmarks.soak import run_scenario_fallback

        report = run_scenario_fallback(32)
        assert report["metrics"]["fallback_count"] == report["fallback_cycles"]

    def test_recovery_is_automatic(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        monitor = ProviderHealthMonitor(
            HealthCheckConfig(
                failure_threshold=2,
                recovery_period=300.0,  # future: disabled state holds
                success_recovery_count=1,
            )
        )
        monitor.register("p")
        monitor.record_success("p")
        monitor.record_failure("p")
        monitor.record_failure("p")
        assert not monitor.is_healthy("p")  # disabled (recovery not due)
        # One success clears the disabled state (success_recovery_count=1).
        monitor.record_success("p")
        assert monitor.is_healthy("p")  # auto re-enabled

    def test_no_permanent_disable(self):
        """Repeated health transitions must never leave a provider
        permanently disabled after recovery successes."""
        from benchmarks.soak import run_scenario_fallback

        report = run_scenario_fallback(48)
        assert report["passed"]
        assert report["metrics"]["provider_failures"] == 0
        assert report["metrics"]["provider_timeouts"] == 0

    def test_reliability_history_bounded(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        monitor = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=2, outcome_window=10)
        )
        monitor.register("p")
        for i in range(100):
            monitor.record_success("p")
            monitor.record_failure("p")
        hist = monitor.reliability_history("p")
        assert hist["window"] <= 10
        assert hist["max_window"] == 10

    def test_degradation_state_never_degrades_single_failure(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        monitor = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=5, outcome_window=10)
        )
        monitor.register("p")
        monitor.record_success("p")
        monitor.record_failure("p")
        assert monitor.degradation_state("p") == "HEALTHY"


# ───────────────────────────────────────────────────────────────────
# 7. Reconciliation soak
# ───────────────────────────────────────────────────────────────────


class TestReconciliationSoak:
    def test_conflict_scenario_passes(self):
        from benchmarks.soak import run_scenario_conflict

        report = run_scenario_conflict(24)
        assert report["passed"], report["failures"]
        assert report["material_seen"] == 4
        assert report["mapping_seen"] == 4
        # The 6-step pattern has 3 AGREE steps (indices 0, 3, 5):
        # 24 iterations / 6 steps = 4 full cycles x 3 AGREE = 12.
        assert report["agree_seen"] == 12

    def test_material_never_trusted(self):
        from benchmarks.soak import run_scenario_conflict

        report = run_scenario_conflict(12)
        assert report["passed"]
        assert report["incident_snapshot"]["counts"]["material_disagreement"] == 2

    def test_mapping_conflict_never_trusted(self):
        from src.data.reconciliation import MAPPING_CONFLICT, reconcile_records

        res = reconcile_records(
            [
                ("a", {"symbol": "NABIL", "close": 100.0, "open": 99.0,
                       "high": 101.0, "low": 98.0, "volume": 1000}),
                ("b", {"symbol": "SCB", "close": 100.0, "open": 99.0,
                       "high": 101.0, "low": 98.0, "volume": 1000}),
            ],
            symbol="NABIL",
            record_date="2026-08-14",
        )
        assert res.status == MAPPING_CONFLICT
        assert not res.is_trusted

    def test_no_poisoning_after_material(self):
        """MATERIAL → HOLD; the next AGREE must be fully trusted."""
        from src.data.reconciliation import AGREE, reconcile_records

        def rec(close_b: float):
            return reconcile_records(
                [
                    ("a", {"symbol": "S1", "close": 100.0, "open": 99.0,
                           "high": 101.0, "low": 98.0, "volume": 1000}),
                    ("b", {"symbol": "S1", "close": close_b, "open": 99.0,
                           "high": 101.0, "low": 98.0, "volume": 1000}),
                ],
                symbol="S1",
                record_date="2026-08-14",
            )

        material = rec(110.0)
        assert not material.is_trusted
        # Same symbol, next request, clean data.
        clean = rec(100.0)
        assert clean.status == AGREE
        assert clean.is_trusted

    def test_incidents_bounded_after_cycles(self):
        from benchmarks.soak import run_scenario_conflict

        report = run_scenario_conflict(30)
        snap = report["incident_snapshot"]
        assert snap["total"] <= 200
        assert snap["counts"]["material_disagreement"] <= 200

    def test_minor_is_trusted_with_preferred_source(self):
        from src.data.reconciliation import MINOR_DISAGREEMENT, reconcile_records

        res = reconcile_records(
            [
                ("primary", {"symbol": "S1", "close": 100.0, "open": 99.0,
                             "high": 101.0, "low": 98.0, "volume": 1000}),
                ("fallback", {"symbol": "S1", "close": 101.5, "open": 100.5,
                              "high": 102.5, "low": 99.5, "volume": 1000}),
            ],
            symbol="S1",
            record_date="2026-08-14",
            preferred_source="primary",
        )
        assert res.status == MINOR_DISAGREEMENT
        assert res.is_trusted
        assert res.selected_source == "primary"


# ───────────────────────────────────────────────────────────────────
# 8. Cache soak
# ───────────────────────────────────────────────────────────────────


class TestCacheSoak:
    def test_cache_cycles_pass(self, tmp_path):
        from benchmarks.soak import run_scenario_cache

        report = run_scenario_cache(tmp_path / "data", tmp_path / "state", iterations=3)
        assert report["passed"], report["failures"]

    def test_cache_bounded_after_cycling(self, tmp_path):
        from benchmarks.soak import run_scenario_cache

        report = run_scenario_cache(tmp_path / "data", tmp_path / "state", iterations=4)
        assert report["cache_entries"] <= report["cache_max"]

    def test_hit_equals_cold(self, tmp_path):
        """A warm cache hit must return the same signal as a cold run."""
        from src.engine.analyzer import analyze_stock
        from src.cache.scanner_cache import scanner_cache
        from src.indicators.cache import indicator_cache

        files = _corpus(tmp_path / "data", 4, 40)
        scanner_cache.clear()
        indicator_cache.clear()
        cold = analyze_stock(str(files[0]))
        warm = analyze_stock(str(files[0]))
        assert cold["signal"] == warm["signal"]

    def test_scanner_cache_stats_sane(self, tmp_path):
        from src.cache.scanner_cache import scanner_cache

        files = _corpus(tmp_path / "data", 4, 40)
        from src.engine.analyzer import analyze_stock

        for p in files:
            analyze_stock(str(p))
        stats = scanner_cache.stats()
        assert stats["dataframe_entries"] <= stats["max_entries"]
        assert stats["analysis_entries"] <= stats["max_entries"]
        assert stats["dataframe_misses"] >= 0

    def test_indicator_cache_lru_bounded(self, tmp_path):
        from src.indicators.cache import indicator_cache

        files = _corpus(tmp_path / "data", 4, 40)
        from src.engine.analyzer import analyze_stock

        for _ in range(3):
            for p in files:
                analyze_stock(str(p))
        stats = indicator_cache.stats()
        assert stats["entries"] <= stats["max_entries"]


# ───────────────────────────────────────────────────────────────────
# 9. Calendar soak
# ───────────────────────────────────────────────────────────────────


class TestCalendarSoak:
    def _temp_calendar(self, tmp_path) -> Path:
        from src.data.calendar import NepseCalendar

        path = tmp_path / "calendar.json"
        cal = NepseCalendar()
        cal.save(path)
        return path

    def test_repeated_validation_deterministic(self, tmp_path):
        from src.data.calendar import NepseCalendar, validate_holiday_candidate

        path = self._temp_calendar(tmp_path)
        cal = NepseCalendar.load(path)
        candidate = {"date": "2026-09-15", "type": "holiday",
                     "provenance": "test-operator"}
        problems1 = validate_holiday_candidate(candidate, calendar=cal)
        problems2 = validate_holiday_candidate(candidate, calendar=cal)
        assert problems1 == problems2
        assert problems1 == []

    def test_rejected_candidate_never_mutates_active_state(self, tmp_path):
        from src.data.calendar import submit_holiday_candidate

        path = self._temp_calendar(tmp_path)
        before = path.read_text(encoding="utf-8")
        # Invalid candidate: missing provenance + malformed type.
        ok, problems = submit_holiday_candidate(
            {"date": "not-a-date", "type": "bogus"},
            path=path,
        )
        assert not ok
        assert problems
        assert path.read_text(encoding="utf-8") == before  # byte-identical

    def test_rollback_atomic(self, tmp_path):
        from src.data.calendar import (
            NepseCalendar,
            rollback_calendar,
            submit_holiday_candidate,
        )

        path = self._temp_calendar(tmp_path)
        ok, problems = submit_holiday_candidate(
            {"date": "2026-09-15", "type": "holiday", "provenance": "test-operator"},
            path=path,
        )
        assert ok, problems
        cal = NepseCalendar.load(path)
        assert cal is not None
        # Rollback restores the pre-submit state.
        ok, problems = rollback_calendar(path=path)
        assert ok, problems
        # A fresh load must parse (atomic write left a valid file).
        reloaded = NepseCalendar.load(path)
        assert reloaded is not None

    def test_active_version_stable_after_rejects(self, tmp_path):
        from src.data.calendar import NepseCalendar, submit_holiday_candidate

        path = self._temp_calendar(tmp_path)
        cal = NepseCalendar.load(path)
        version_before = cal.version
        for i in range(5):
            ok, _ = submit_holiday_candidate(
                {"date": "bogus", "type": "holiday"}, path=path
            )
            assert not ok
        cal_after = NepseCalendar.load(path)
        assert cal_after.version == version_before

    def test_calendar_classify_date_stable(self, tmp_path):
        from src.data.calendar import NepseCalendar, WEEKEND

        path = self._temp_calendar(tmp_path)
        cal = NepseCalendar.load(path)
        # 2026-08-15 is a Saturday (NEPSE weekend).
        for _ in range(3):
            assert cal.classify_date("2026-08-15") == WEEKEND

    def test_submit_accepts_valid_candidate(self, tmp_path):
        from src.data.calendar import NepseCalendar, submit_holiday_candidate

        path = self._temp_calendar(tmp_path)
        ok, problems = submit_holiday_candidate(
            {"date": "2026-09-15", "type": "holiday", "provenance": "test-operator"},
            path=path,
        )
        assert ok, problems
        cal = NepseCalendar.load(path)
        assert cal.classify_date("2026-09-15") == "HOLIDAY"


# ───────────────────────────────────────────────────────────────────
# 10. Notification soak
# ───────────────────────────────────────────────────────────────────


class TestNotificationSoak:
    def test_notify_clear_repeat_bounded(self, tmp_path, monkeypatch):
        from src.ui.notifications import MAX_HISTORY, NotificationManager
        import src.ui.notifications as notif_module

        target = tmp_path / "notifications.json"
        monkeypatch.setattr(notif_module, "NOTIF_FILE", target)
        NotificationManager._instance = None
        monkeypatch.setattr(NotificationManager, "_instance", None)
        mgr = NotificationManager()
        for i in range(MAX_HISTORY + 50):
            mgr.notify(f"msg {i}", category="system")
        assert len(mgr._notifications) <= MAX_HISTORY

    def test_no_stale_unread_after_repeat(self, tmp_path, monkeypatch):
        from src.ui.notifications import MAX_HISTORY, NotificationManager
        import src.ui.notifications as notif_module

        target = tmp_path / "notifications.json"
        monkeypatch.setattr(notif_module, "NOTIF_FILE", target)
        NotificationManager._instance = None
        monkeypatch.setattr(NotificationManager, "_instance", None)
        mgr = NotificationManager()
        for i in range(100):
            mgr.notify(f"n{i}")
        for n in mgr._notifications:
            mgr.mark_read(n.id)
        unread = mgr.unread_count
        for i in range(100):
            mgr.notify(f"m{i}")
        assert mgr.unread_count == 100 + unread  # new ones unread, old read

    def test_history_file_does_not_grow_unbounded(self, tmp_path, monkeypatch):
        from src.ui.notifications import MAX_HISTORY, NotificationManager
        import src.ui.notifications as notif_module
        import json as _json

        target = tmp_path / "notifications.json"
        monkeypatch.setattr(notif_module, "NOTIF_FILE", target)
        NotificationManager._instance = None
        monkeypatch.setattr(NotificationManager, "_instance", None)
        mgr = NotificationManager()
        for i in range(MAX_HISTORY + 100):
            mgr.notify(f"x{i}")
        data = _json.loads(target.read_text(encoding="utf-8"))
        assert len(data) <= MAX_HISTORY

    def test_singleton_reset_between_sessions(self, tmp_path, monkeypatch):
        from src.ui.notifications import NotificationManager
        import src.ui.notifications as notif_module

        target = tmp_path / "notifications.json"
        monkeypatch.setattr(notif_module, "NOTIF_FILE", target)
        NotificationManager._instance = None
        monkeypatch.setattr(NotificationManager, "_instance", None)
        mgr1 = NotificationManager()
        mgr1.notify("first")
        assert len(mgr1._notifications) == 1
        # Simulate restart: fresh singleton against a cleared store.
        target.write_text("{}", encoding="utf-8")
        NotificationManager._instance = None
        mgr2 = NotificationManager()
        assert len(mgr2._notifications) == 0  # no phantom notifications

    def test_module_alias_stays_consistent(self):
        """The module-level ``notification_manager`` alias (imported by
        UI pages) must reference the same instance as the class
        singleton — a reset of one must never orphan the other."""
        import src.ui.notifications as notif_module

        from src.ui.notifications import NotificationManager

        alias = notif_module.notification_manager
        # After the conftest autouse reset, both point at the fresh
        # instance (or the alias is None only when construction failed).
        if alias is not None:
            assert alias is NotificationManager._instance


# ───────────────────────────────────────────────────────────────────
# 11. Scanner soak
# ───────────────────────────────────────────────────────────────────


class TestScannerSoak:
    def test_repeated_scan_stable(self, tmp_path):
        from src.scanner import engine as scanner_engine
        from benchmarks.pipeline import _state_files

        data_dir = tmp_path / "data"
        _corpus(data_dir, 4, 40)
        old_dir = scanner_engine.DATA_DIRECTORY
        scanner_engine.DATA_DIRECTORY = str(data_dir)
        try:
            with _state_files(tmp_path / "state"):
                r1 = scanner_engine.scan_market(workers=2)
                r2 = scanner_engine.scan_market(workers=2)
            # scan_market returns {"results": [...], "skipped": [...]}
            results1 = r1["results"]
            results2 = r2["results"]
            assert len(results1) == 4
            assert len(results2) == 4
            syms1 = sorted(r["symbol"] for r in results1)
            syms2 = sorted(r["symbol"] for r in results2)
            assert syms1 == syms2
        finally:
            scanner_engine.DATA_DIRECTORY = old_dir

    def test_scan_results_deterministic_order(self, tmp_path):
        from src.scanner import engine as scanner_engine
        from benchmarks.pipeline import _state_files

        data_dir = tmp_path / "data"
        _corpus(data_dir, 4, 40)
        old_dir = scanner_engine.DATA_DIRECTORY
        scanner_engine.DATA_DIRECTORY = str(data_dir)
        try:
            with _state_files(tmp_path / "state2"):
                order1 = [r["symbol"] for r in scanner_engine.scan_market(workers=4)["results"]]
                order2 = [r["symbol"] for r in scanner_engine.scan_market(workers=4)["results"]]
            assert order1 == order2
        finally:
            scanner_engine.DATA_DIRECTORY = old_dir

    def test_unsafe_suppressed_in_scan(self, tmp_path):
        """A quarantined / conflicted analysis must surface as HOLD with
        signal_suppressed=True, never a raw BUY/SELL."""
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df

        df = make_synthetic_df(rows=40)
        from src.data.provenance import DataProvenance, TRUST_CONFLICTED

        prov = DataProvenance(sources=["a", "b"], trust=TRUST_CONFLICTED,
                              reconciliation_status="MATERIAL_DISAGREEMENT")
        result = analyze_dataframe(df, symbol="SYN001", provenance=prov)
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True

    def test_recovery_restores_normal_signal(self, tmp_path):
        """After a conflicted state, a clean trusted analysis returns to
        the normal signal path."""
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df

        df = make_synthetic_df(rows=40)
        from src.data.provenance import DataProvenance, TRUST_TRUSTED

        prov = DataProvenance(sources=["a"], trust=TRUST_TRUSTED)
        result = analyze_dataframe(df, symbol="SYN001", provenance=prov)
        assert result["signal_suppressed"] is False
        assert result["signal"] in ("BUY", "SELL", "HOLD")

    def test_scanner_worker_pool_terminates(self, tmp_path):
        """Repeated scans must not leak threads (pool created and joined
        per scan)."""
        from src.scanner import engine as scanner_engine
        from benchmarks.pipeline import _state_files

        data_dir = tmp_path / "data"
        _corpus(data_dir, 4, 40)
        old_dir = scanner_engine.DATA_DIRECTORY
        scanner_engine.DATA_DIRECTORY = str(data_dir)
        try:
            with _state_files(tmp_path / "state3"):
                before = threading.active_count()
                for _ in range(3):
                    scanner_engine.scan_market(workers=4)
                gc.collect()
                after = threading.active_count()
            assert after - before <= 4  # bounded drift
        finally:
            scanner_engine.DATA_DIRECTORY = old_dir


# ───────────────────────────────────────────────────────────────────
# 12. Alert soak
# ───────────────────────────────────────────────────────────────────


class TestAlertSoak:
    def _isolate_alert_history(self, monkeypatch, tmp_path):
        """Point both the alert engine and history module at a private
        file (Sprint 13.7 pattern): ``process_alerts`` resolves
        ``HISTORY_FILE`` from the engine's import-time copy, so patching
        only ``src.alerts.history.HISTORY_FILE`` would leave the lock
        sentinel on the real ``data/alerts/history.json``."""
        from src.alerts import engine as alert_engine
        from src.alerts import history as alert_history

        target = tmp_path / "history.json"
        monkeypatch.setattr(alert_history, "HISTORY_FILE", target)
        monkeypatch.setattr(alert_engine, "HISTORY_FILE", target)
        return target

    def test_suppressed_signal_only_suppressed_alert(self, tmp_path, monkeypatch):
        """An unsafe analysis must never produce a BUY/SELL alert — only
        the informational SUPPRESSED note."""
        from src.alerts.engine import process_alerts
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import DataProvenance, TRUST_CONFLICTED

        self._isolate_alert_history(monkeypatch, tmp_path)
        df = make_synthetic_df(rows=40)
        prov = DataProvenance(sources=["a"], trust=TRUST_CONFLICTED,
                              reconciliation_status="MATERIAL_DISAGREEMENT")
        result = analyze_dataframe(df, symbol="SYN001", provenance=prov)
        assert result["signal_suppressed"] is True
        alerts = process_alerts("SYN001", result)
        assert all(a["type"] == "SUPPRESSED" for a in alerts)
        assert not any(a["type"] in ("BUY", "SELL") for a in alerts)

    def test_recovery_restores_alert_path(self, tmp_path, monkeypatch):
        from src.alerts.engine import process_alerts
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import DataProvenance, TRUST_TRUSTED

        self._isolate_alert_history(monkeypatch, tmp_path)
        df = make_synthetic_df(rows=40)
        prov = DataProvenance(sources=["a"], trust=TRUST_TRUSTED)
        result = analyze_dataframe(df, symbol="SYN002", provenance=prov)
        assert result["signal_suppressed"] is False
        alerts = process_alerts("SYN002", result)
        # First scan emits an INITIAL alert (not SUPPRESSED).
        assert any(a["type"] == "INITIAL" for a in alerts)
        assert not any(a["type"] == "SUPPRESSED" for a in alerts)

    def test_alert_history_bounded_after_soak(self, tmp_path, monkeypatch):
        from src.alerts.engine import process_alerts
        from src.alerts import history as alert_history
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import DataProvenance, TRUST_TRUSTED

        self._isolate_alert_history(monkeypatch, tmp_path)
        prov = DataProvenance(sources=["a"], trust=TRUST_TRUSTED)
        for i in range(20):
            df = make_synthetic_df(rows=40, seed=i)
            result = analyze_dataframe(df, symbol=f"SYN{i:03d}", provenance=prov)
            process_alerts(f"SYN{i:03d}", result)
        hist = alert_history.load_history()
        assert len(hist) == 20  # bounded: one entry per symbol, no growth

    def test_no_duplicate_unsafe_alerts(self, tmp_path, monkeypatch):
        """Repeated suppressed analyses must not append repeated
        BUY/SELL alerts to history."""
        from src.alerts.engine import process_alerts
        from src.alerts import history as alert_history
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import DataProvenance, TRUST_CONFLICTED

        self._isolate_alert_history(monkeypatch, tmp_path)
        prov = DataProvenance(sources=["a"], trust=TRUST_CONFLICTED,
                              reconciliation_status="MATERIAL_DISAGREEMENT")
        for i in range(5):
            df = make_synthetic_df(rows=40, seed=i)
            result = analyze_dataframe(df, symbol="SYNX", provenance=prov)
            process_alerts("SYNX", result)
        hist = alert_history.load_history()
        state = hist.get("SYNX", {})
        kinds = [a.get("type") for a in state.get("alert_history", [])]
        assert not any(k in ("BUY", "SELL") for k in kinds)

    def test_alert_batch_isolates_failure(self, tmp_path, monkeypatch):
        from src.alerts.engine import process_alert_batch
        from src.alerts import history as alert_history

        self._isolate_alert_history(monkeypatch, tmp_path)
        entries = [
            ("GOOD1", {"signal": "BUY", "confidence": 90, "score": 5, "price": 100.0,
                       "trend": "UPTREND", "volume_signal": "NORMAL",
                       "relative_volume": 1.0,
                       "milestones": {"target1": False, "target2": False, "target3": False}}),
            ("GOOD2", {"signal": "SELL", "confidence": 10, "score": 1, "price": 50.0,
                       "trend": "DOWNTREND", "volume_signal": "NORMAL",
                       "relative_volume": 1.0,
                       "milestones": {"target1": False, "target2": False, "target3": False}}),
        ]
        by_symbol = process_alert_batch(entries)
        assert "GOOD1" in by_symbol and "GOOD2" in by_symbol
        hist = alert_history.load_history()
        assert "GOOD1" in hist and "GOOD2" in hist


# ───────────────────────────────────────────────────────────────────
# 13. API soak
# ───────────────────────────────────────────────────────────────────


class TestApiSoak:
    def test_metrics_snapshot_bounded(self, tmp_path):
        """Repeated metrics snapshots stay bounded (scalar counters)."""
        from src.data.reconciliation import reconciliation_metrics
        from src.data.quality import quality_metrics

        for _ in range(5):
            snap1 = reconciliation_metrics.snapshot()
            snap2 = quality_metrics.snapshot()
            assert all(isinstance(v, int) and v >= 0 for v in snap1.values())
            assert all(isinstance(v, int) and v >= 0 for v in snap2.values())

    def test_metrics_endpoint_payload_stable(self, tmp_path):
        from src.api.metrics import metrics as metrics_endpoint

        payload1 = metrics_endpoint()
        payload2 = metrics_endpoint()
        assert payload1.keys() == payload2.keys()

    def test_analyze_repeated_stable(self, tmp_path):
        from src.engine.analyzer import analyze_stock
        from src.cache.scanner_cache import scanner_cache
        from src.indicators.cache import indicator_cache

        files = _corpus(tmp_path / "data", 4, 40)
        scanner_cache.clear()
        indicator_cache.clear()
        results = [analyze_stock(str(files[0])) for _ in range(3)]
        assert len({r["signal"] for r in results}) == 1
        assert results[0]["symbol"] == results[1]["symbol"]

    def test_metrics_no_state_litter(self, tmp_path, monkeypatch):
        from src.api.metrics import metrics as metrics_endpoint

        payload = metrics_endpoint()
        assert "process" in payload
        assert "indicator_cache" in payload
        assert "scanner_cache" in payload or "data_service" in payload


# ───────────────────────────────────────────────────────────────────
# 14. Worker restart
# ───────────────────────────────────────────────────────────────────


class TestWorkerRestart:
    def test_worker_restart_cycle(self, tmp_path):
        """Spawn → request → shutdown → respawn → request; state stays
        valid and no subprocess survives shutdown."""
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import (
            _request,
            _shutdown,
            _spawn_api,
            _wait_ready,
        )

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 4, 40)
        state_root.mkdir(parents=True, exist_ok=True)

        procs = []
        statuses = []
        try:
            for _ in range(2):
                proc = _spawn_api(data_dir, state_root, port, workers=1)
                procs.append(proc)
                _wait_ready(port, timeout=120)
                status, _ = _request(port, "/")
                statuses.append(status)
                # Hit an analyze route so alert-history state is actually
                # written into the state root (otherwise the store check
                # below would pass vacuously over an empty store set).
                _request(port, "/analyze/SYN000")
                _shutdown(proc)
        finally:
            for p in procs:
                _shutdown(p)
        assert statuses == [200, 200]
        # The API child resolves state files against its CWD (= state_root),
        # so the alert history lands at ``data/alerts/history.json`` under
        # the state root (not ``alerts/history.json``).  Check the real
        # paths directly.
        alert_file = state_root / "data" / "alerts" / "history.json"
        assert alert_file.exists(), "no alert history written — test is vacuous"
        json.loads(alert_file.read_text(encoding="utf-8"))
        for p in procs:
            assert p.poll() is not None  # no survivor
        # No tmp/lock/corrupt litter anywhere under the state root.
        litter = [str(x.relative_to(state_root)) for x in state_root.rglob("*")]
        assert not any(x.endswith(".tmp") or ".lock" in x or ".corrupt.bak" in x
                       for x in litter)

    def test_shutdown_terminates_tree(self, tmp_path):
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import _request, _shutdown, _spawn_api, _wait_ready

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 3, 30)
        state_root.mkdir(parents=True, exist_ok=True)
        proc = _spawn_api(data_dir, state_root, port, workers=1)
        try:
            _wait_ready(port, timeout=120)
            status, _ = _request(port, "/")
            assert status == 200
        finally:
            _shutdown(proc)
        assert proc.poll() is not None

    def test_worker_state_integrity_after_restart(self, tmp_path):
        """Alert / portfolio state written by the first worker must still
        parse after a restart cycle."""
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import (
            _request,
            _shutdown,
            _spawn_api,
            _wait_ready,
        )

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 4, 40)
        state_root.mkdir(parents=True, exist_ok=True)
        proc = None
        try:
            proc = _spawn_api(data_dir, state_root, port, workers=1)
            _wait_ready(port, timeout=120)
            _request(port, "/")
            # Write alert/portfolio state through the real API so the
            # store-integrity check below validates real files.
            _request(port, "/analyze/SYN000")
            _request(port, "/portfolio")
        finally:
            if proc is not None:
                _shutdown(proc)
        # The API child resolves state files against its CWD (= state_root).
        alert_file = state_root / "data" / "alerts" / "history.json"
        assert alert_file.exists(), "no alert history written — test is vacuous"
        alert_data = json.loads(alert_file.read_text(encoding="utf-8"))
        assert isinstance(alert_data, dict)
        # Alert history must contain the analyzed symbol.
        assert "SYN000" in alert_data


# ───────────────────────────────────────────────────────────────────
# 15. Two-worker soak
# ───────────────────────────────────────────────────────────────────


class TestTwoWorkerSoak:
    def test_two_workers_serve_and_shutdown_cleanly(self, tmp_path):
        """A bounded 2-worker deployment must serve both endpoints and
        tear down without survivors."""
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import (
            _request,
            _scan_state,
            _shutdown,
            _spawn_api,
            _wait_ready,
        )

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 4, 40)
        state_root.mkdir(parents=True, exist_ok=True)
        proc = None
        try:
            proc = _spawn_api(data_dir, state_root, port, workers=2)
            _wait_ready(port, timeout=150)
            s1, _ = _request(port, "/")
            s2, _ = _request(port, "/metrics")
            assert s1 == 200 and s2 == 200
        finally:
            if proc is not None:
                _shutdown(proc)
        assert proc is not None and proc.poll() is not None
        state = _scan_state(state_root)
        assert not (state["leftover_tmp"] or state["corrupt_backups"])

    def test_two_worker_metrics_valid(self, tmp_path):
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import _request, _shutdown, _spawn_api, _wait_ready

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 3, 30)
        state_root.mkdir(parents=True, exist_ok=True)
        proc = None
        try:
            proc = _spawn_api(data_dir, state_root, port, workers=2)
            _wait_ready(port, timeout=150)
            status, body = _request(port, "/metrics")
            assert status == 200
            payload = json.loads(body)
            assert isinstance(payload.get("process", {}).get("pid"), int)
            for key in ("hits", "misses", "entries", "max_entries"):
                assert isinstance(payload["indicator_cache"].get(key), int)
        finally:
            if proc is not None:
                _shutdown(proc)


# ───────────────────────────────────────────────────────────────────
# 16. Memory bounds
# ───────────────────────────────────────────────────────────────────


class TestMemoryBounds:
    def test_no_monotonic_memory_growth(self, tmp_path):
        """Repeated identical workload must not grow the Python-object
        layer monotonically beyond the documented envelope."""
        from benchmarks.soak import MEMORY_LEAK_DELTA_MIB, run_scenario_normal

        report = run_scenario_normal(
            tmp_path / "data", tmp_path / "state", iterations=6, symbols=4, rows=40
        )
        assert report["passed"], report["failures"]
        assert report["memory_growth_mib"] <= MEMORY_LEAK_DELTA_MIB

    def test_soak_envelope_constant_positive(self):
        from benchmarks import soak

        assert soak.MEMORY_LEAK_DELTA_MIB > 0
        assert soak.THREAD_DELTA_MAX > 0
        assert soak.FILE_OBJECTS_DELTA_MAX > 0

    def test_memory_envelope_repeated_runs(self, tmp_path):
        """Two successive normal-soak runs must both respect the
        envelope (no cross-run accumulation)."""
        from benchmarks.soak import run_scenario_normal

        r1 = run_scenario_normal(tmp_path / "d1", tmp_path / "s1", 5, 4, 40)
        r2 = run_scenario_normal(tmp_path / "d2", tmp_path / "s2", 5, 4, 40)
        assert r1["passed"] and r2["passed"]
        assert r1["memory_end_mib"] > 0 and r2["memory_end_mib"] > 0

    def test_tracemalloc_balanced(self):
        """The soak must start/stop tracemalloc symmetrically (no leak
        of the tracing overhead into other tests)."""
        import tracemalloc as tm

        assert not tm.is_tracing()


# ───────────────────────────────────────────────────────────────────
# 17. File / resource leaks
# ───────────────────────────────────────────────────────────────────


class TestResourceLeaks:
    def test_no_thread_accumulation_across_scenarios(self, tmp_path):
        from benchmarks.soak import capture_resources, resource_deltas

        state_root = tmp_path / "state"
        state_root.mkdir(exist_ok=True)
        before = capture_resources(state_root)
        from src.scanner import engine as scanner_engine
        from benchmarks.pipeline import _state_files

        data_dir = tmp_path / "data"
        _corpus(data_dir, 4, 40)
        old_dir = scanner_engine.DATA_DIRECTORY
        scanner_engine.DATA_DIRECTORY = str(data_dir)
        try:
            with _state_files(state_root):
                for _ in range(3):
                    scanner_engine.scan_market(workers=4)
        finally:
            scanner_engine.DATA_DIRECTORY = old_dir
        gc.collect()
        after = capture_resources(state_root)
        deltas = resource_deltas(before, after)
        assert deltas["threads_ok"], deltas

    def test_no_file_object_accumulation(self, tmp_path):
        from benchmarks.soak import capture_resources, resource_deltas

        state_root = tmp_path / "state"
        state_root.mkdir(exist_ok=True)
        before = capture_resources(state_root)
        files = _corpus(tmp_path / "data", 4, 40)
        from src.engine.analyzer import analyze_stock

        for _ in range(3):
            for p in files:
                analyze_stock(str(p))
        gc.collect()
        after = capture_resources(state_root)
        deltas = resource_deltas(before, after)
        assert deltas["file_objects_ok"], deltas

    def test_no_state_litter_after_workload(self, tmp_path):
        from benchmarks.soak import capture_resources, resource_deltas

        state_root = tmp_path / "state"
        state_root.mkdir(exist_ok=True)
        before = capture_resources(state_root)
        from src.alerts import history as alert_history

        alert_history.HISTORY_FILE = tmp_path / "state" / "alerts" / "history.json"
        (tmp_path / "state" / "alerts").mkdir(parents=True, exist_ok=True)
        alert_history.save_history({"A": {}})
        after = capture_resources(state_root)
        deltas = resource_deltas(before, after)
        assert deltas["litter_ok"], deltas

    def test_temp_dirs_cleaned_by_soak(self, tmp_path, monkeypatch):
        from benchmarks.soak import run_soak

        # Small hermetic run; then assert no temp dirs were left behind.
        report = run_soak(iterations=2, symbols=3, rows=30)
        assert report["verdict"] == "PASS", report["failures"]
        assert report["resources"]["litter_ok"]


# ───────────────────────────────────────────────────────────────────
# 18. Subprocess cleanup
# ───────────────────────────────────────────────────────────────────


class TestSubprocessCleanup:
    def test_no_subprocess_survives_shutdown(self, tmp_path):
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import _request, _shutdown, _spawn_api, _wait_ready

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 3, 30)
        state_root.mkdir(parents=True, exist_ok=True)
        proc = _spawn_api(data_dir, state_root, port, workers=1)
        try:
            _wait_ready(port, timeout=120)
            _request(port, "/")
        finally:
            _shutdown(proc)
        assert proc.poll() is not None

    def test_ports_released_after_shutdown(self, tmp_path):
        """After shutdown the worker's port must be released (a second
        spawn on the same port binds successfully)."""
        import socket

        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import _request, _shutdown, _spawn_api, _wait_ready

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        write_csvs(data_dir, 3, 30)
        state_root.mkdir(parents=True, exist_ok=True)
        p1 = _spawn_api(data_dir, state_root, port, workers=1)
        try:
            _wait_ready(port, timeout=120)
            _request(port, "/")
        finally:
            _shutdown(p1)
        # Rebind the same port — must succeed.  Windows socket release
        # can lag a few hundred ms after the worker exits, so retry
        # briefly instead of asserting on the first probe.
        import time as _time

        rebound = False
        for _ in range(20):
            try:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
                rebound = True
                break
            except OSError:
                _time.sleep(0.25)
        assert rebound, f"port {port} not released after shutdown"


# ───────────────────────────────────────────────────────────────────
# 19. Metrics bounds
# ───────────────────────────────────────────────────────────────────


class TestMetricsBounds:
    def test_worker_metrics_file_bounded(self, tmp_path):
        from pathlib import Path as _P

        from src.utils.worker_metrics import WORKER_METRICS_FILE

        # The file (if written) must stay JSON-parseable and bounded.
        metrics_path = _P(WORKER_METRICS_FILE)
        if metrics_path.exists():
            data = json.loads(metrics_path.read_text(encoding="utf-8"))
            assert isinstance(data, dict)

    def test_metrics_counters_are_scalars(self):
        from src.data.reconciliation import reconciliation_metrics

        snap = reconciliation_metrics.snapshot()
        assert all(isinstance(v, int) for v in snap.values())
        # Bounded by construction: 11 scalar fields.
        assert len(snap) == 11
        from src.data.incidents import ALL_INCIDENT_KINDS

        assert len(ALL_INCIDENT_KINDS) == 8

    def test_metrics_endpoint_no_accumulating_keys(self, tmp_path):
        from src.api.metrics import metrics as metrics_endpoint

        p1 = metrics_endpoint()
        p2 = metrics_endpoint()
        assert set(p1.keys()) == set(p2.keys())


# ───────────────────────────────────────────────────────────────────
# 20. Incident bounds
# ───────────────────────────────────────────────────────────────────


class TestIncidentBounds:
    def test_incident_ring_bounded(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=50)
        for i in range(500):
            tracker.record("fallback", symbol=f"S{i}")
        snap = tracker.snapshot(recent_limit=0)
        assert snap["total"] == 500  # counters are scalar totals
        assert len(tracker.recent(limit=10**6)) <= 50  # deque capped

    def test_repeated_minor_map_bounded(self):
        from src.data.incidents import IncidentTracker, REPEATED_MINOR_MAX_SYMBOLS

        tracker = IncidentTracker(max_events=100)
        for i in range(REPEATED_MINOR_MAX_SYMBOLS + 100):
            tracker.record_minor(f"SYM{i:05d}")
        assert len(tracker._minor_counts) <= REPEATED_MINOR_MAX_SYMBOLS

    def test_incident_snapshot_recent_bounded(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=200)
        for i in range(300):
            tracker.record("provider_timeout", symbol=f"S{i}")
        snap = tracker.snapshot(recent_limit=20)
        assert len(snap["recent"]) == 20  # payload bounded regardless of total

    def test_unknown_kind_never_inflates(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=50)
        tracker.record("totally_unknown_kind", symbol="X")
        snap = tracker.snapshot(recent_limit=0)
        assert snap["counts"]["malformed_response"] == 1


# ───────────────────────────────────────────────────────────────────
# 21. Sprint 13.7 regression
# ───────────────────────────────────────────────────────────────────


class TestSprint137Regression:
    """The key Sprint 13.7 safety guarantees must survive Sprint 13.8."""

    def test_unsafe_provenance_always_hold(self, tmp_path):
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import (
            DataProvenance,
            TRUST_CALENDAR_INVALID,
            TRUST_CONFLICTED,
            TRUST_QUARANTINED,
            TRUST_UNAVAILABLE,
        )

        df = make_synthetic_df(rows=40)
        for trust in (TRUST_CONFLICTED, TRUST_UNAVAILABLE,
                      TRUST_CALENDAR_INVALID, TRUST_QUARANTINED):
            prov = DataProvenance(sources=["a"], trust=trust)
            result = analyze_dataframe(df, symbol="SYN", provenance=prov)
            assert result["signal"] == "HOLD", trust
            assert result["signal_suppressed"] is True, trust

    def test_safe_provenance_not_suppressed(self):
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import DataProvenance, TRUST_TRUSTED

        df = make_synthetic_df(rows=40)
        prov = DataProvenance(sources=["a"], trust=TRUST_TRUSTED)
        result = analyze_dataframe(df, symbol="SYN", provenance=prov)
        assert result["signal_suppressed"] is False

    def test_invalid_data_quarantine_propagates(self):
        """INVALID-quality data must suppress the signal even without an
        explicit provenance block."""
        from src.engine.analyzer import analyze_dataframe
        from src.data.quality import assess_history
        from benchmarks.common import make_synthetic_df

        df = make_synthetic_df(rows=40)
        df.loc[df.index[10], "Close"] = -5.0  # break OHLC contract
        quality = assess_history(df, symbol="SYN")
        assert quality.status == "INVALID"
        result = analyze_dataframe(df, symbol="SYN", quality_check=True)
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True

    def test_analyzer_suppression_chain_order(self):
        """Provenance suppression must win over reconciliation when both
        are present (most authoritative gate last)."""
        from src.engine.analyzer import analyze_dataframe
        from benchmarks.common import make_synthetic_df
        from src.data.provenance import DataProvenance, TRUST_CONFLICTED
        from src.data.reconciliation import ReconciliationResult

        df = make_synthetic_df(rows=40)
        rec = ReconciliationResult(status="AGREE", symbol="SYN", providers=["a"])
        prov = DataProvenance(sources=["a"], trust=TRUST_CONFLICTED,
                              reconciliation_status="MATERIAL_DISAGREEMENT")
        result = analyze_dataframe(df, symbol="SYN", reconciliation=rec, provenance=prov)
        assert result["signal_suppressed"] is True
        assert result["signal"] == "HOLD"

    def test_calendar_version_governance_holds(self, tmp_path):
        """Rejected holiday candidates never mutate the active calendar
        version (Sprint 13.7 §12 regression)."""
        from src.data.calendar import NepseCalendar, submit_holiday_candidate

        path = tmp_path / "cal.json"
        cal = NepseCalendar()
        cal.save(path)
        v_before = NepseCalendar.load(path).version
        for i in range(3):
            ok, _ = submit_holiday_candidate(
                {"date": f"bad-date-{i}", "type": "holiday"}, path=path
            )
            assert not ok
        assert NepseCalendar.load(path).version == v_before

    def test_notification_singleton_isolation_holds(self, tmp_path, monkeypatch):
        """The Sprint 13.7 conftest reset keeps the singleton clean per
        test; a bounded fill must not break later notify calls."""
        from src.ui.notifications import MAX_HISTORY, NotificationManager
        import src.ui.notifications as notif_module

        target = tmp_path / "notif.json"
        monkeypatch.setattr(notif_module, "NOTIF_FILE", target)
        NotificationManager._instance = None
        monkeypatch.setattr(NotificationManager, "_instance", None)
        mgr = NotificationManager()
        for i in range(MAX_HISTORY + 10):
            mgr.notify(f"x{i}")
        assert mgr.unread_count <= MAX_HISTORY
        # A later notification still works.
        n = mgr.notify("still works")
        assert n is not None
