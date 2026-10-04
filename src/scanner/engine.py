"""Market scanner engine.

Scans every OHLCV CSV in ``DATA_DIRECTORY`` and ranks the market.

Performance notes (Sprint 11):

- **Parallelism** — files are analysed concurrently with a bounded
  ``ThreadPoolExecutor`` (worker count from ``SCANNER_WORKERS``),
  replacing the previous sequential ``for`` loop.
- **Cache** — parsed DataFrames and analysis outputs are cached by
  file fingerprint through :class:`src.cache.scanner_cache.ScannerCache`
  (TTL + LRU), so repeated scans of unchanged files skip parsing and
  re-analysis entirely.
- **Determinism** — results are collected in file order (the ranking
  sort in ``rank_market`` is stable), so output is deterministic
  regardless of worker scheduling.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from src.alerts.engine import process_alert_batch
from src.cache.scanner_cache import scanner_cache
from src.config import (
    DATA_DIRECTORY,
    SCANNER_LOG_PROGRESS_EVERY,
    SCANNER_WORKERS,
)
from src.engine.analyzer import analyze_stock
from src.logging.logger import logger
from src.scanner.ranking import rank_market


def get_stock_files():
    """
    Return every CSV file inside DATA_DIRECTORY.
    """
    data_path = Path(DATA_DIRECTORY)
    return sorted(data_path.glob("*.csv"))


def _analyze_file(file: Path) -> tuple[dict | None, dict | None, bool]:
    """Analyse a single CSV file (runs inside a worker thread).

    Returns ``(result, skipped_entry, fresh)``.  For a normal analysis
    ``skipped_entry`` is ``None``; for an unreadable file both ``result``
    and ``skipped_entry`` are ``None``.  A file whose data fails the
    canonical contract (``data_quality.status == INVALID``) returns
    *both* ``result`` and ``skipped_entry`` — the analysis is kept so it
    can be cached (warm scans stay pure hits) while the skip entry
    excludes it from ranked results.  *fresh* is ``True`` when the
    analysis was computed just now (a cache miss) and therefore still
    needs scan-level alert batching and caching (Sprint 11.3).
    Per-symbol alert processing is deliberately deferred so the whole
    scan writes the alert-history file once instead of once per symbol.

    Args:
        file: Path to the source CSV.

    Returns:
        Tuple of (analysis dict or None, skipped entry or None,
        fresh flag).
    """
    symbol = file.stem.upper()
    try:
        analysis = scanner_cache.get_analysis(file)
        fresh = False
        if analysis is None:
            analysis = analyze_stock(str(file), with_alerts=False)
            fresh = True
        # Sprint 13.3 (Phase 15): a file whose data fails the canonical
        # market-data contract (broken OHLC, NaN, conflicting duplicates)
        # must not enter the ranked results.  The analyzer suppresses its
        # signal, and the scanner moves it to ``skipped`` so operators see
        # exactly why it was excluded.  Healthy symbols are unaffected and
        # deterministic ranking is preserved for valid symbols.
        dq = (analysis or {}).get("data_quality") or {}
        if dq.get("status") == "INVALID":
            return analysis, {
                "symbol": symbol,
                "error": "invalid_data",
                "reasons": dq.get("reasons", []),
            }, fresh
        return analysis, None, fresh
    except Exception as exc:  # noqa: BLE001 - per-file isolation
        logger.exception("Error analyzing %s", symbol)
        return None, {"symbol": symbol, "error": str(exc)}, False


def scan_market(workers: int | None = None) -> dict:
    """Scan every stock CSV and return ranked results.

    Parallelism is bounded and deterministic: files are analysed with a
    ``ThreadPoolExecutor`` (worker count from *workers* or
    ``SCANNER_WORKERS``) and results are collected in file order, so the
    output is identical regardless of worker scheduling.  A value of 1
    (or ``SCANNER_WORKERS=1``) selects a safe sequential fallback that
    analyses files one at a time in the calling thread.

    Args:
        workers: Optional worker count override.  ``None`` uses
            ``SCANNER_WORKERS``.  ``1`` forces sequential execution.

    Returns:
        A dict with ``results`` (ranked analyses, file order) and
        ``skipped`` (per-file failure entries).
    """
    files = [f for f in get_stock_files() if f.stem.lower() != "sample"]

    results: list[dict] = []
    skipped: list[dict] = []
    # (file, analysis) pairs computed on this scan (cache misses) that
    # still need scan-level alert batching + caching (Sprint 11.3).
    fresh: list[tuple[Path, dict]] = []

    if not files:
        return {"results": rank_market(results), "skipped": skipped}

    # An explicit 0 is invalid; clamp to 1 (sequential) rather than
    # silently falling back to the configured default.
    workers = max(1, SCANNER_WORKERS if workers is None else workers)
    start = time.perf_counter()
    logger.info(
        "Scanning %d files with %d worker(s)...",
        len(files),
        workers,
    )

    def _collect(index: int, file: Path, analysis: dict | None, skip: dict | None, is_fresh: bool) -> None:
        if analysis is not None and skip is None:
            analysis["symbol"] = file.stem.upper()
            results.append(analysis)
        if skip is not None:
            skipped.append(skip)
        # INVALID-quality analyses are still cached (so a warm scan is a
        # pure cache hit — Sprint 12.3 gate) but they carry a skip entry
        # and never enter the ranked results (Sprint 13.3 Phase 15).
        if is_fresh and analysis is not None:
            fresh.append((file, analysis))
        if index % max(1, SCANNER_LOG_PROGRESS_EVERY) == 0:
            logger.info("Scan progress: %d/%d analysed", index, len(files))

    if workers == 1:
        # Sequential fallback: analyse in the calling thread, one file at
        # a time.  Per-file exception isolation is identical to the
        # parallel path (one bad symbol never aborts the scan).
        for index, file in enumerate(files, start=1):
            analysis, skip, is_fresh = _analyze_file(file)
            _collect(index, file, analysis, skip, is_fresh)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scanner") as executor:
            # executor.map preserves input order -> deterministic output.
            futures = executor.map(_analyze_file, files)
            for index, (analysis, skip, is_fresh) in enumerate(futures, start=1):
                _collect(index, files[index - 1], analysis, skip, is_fresh)

    # Scan-level alert batching (Sprint 11.3): the alert engine loads
    # the history once, updates every fresh symbol in memory and
    # persists once — replacing one history read + write per symbol.
    # Analyses are cached only after ``new_alerts`` is attached so a
    # cache hit always returns the completed (alerted) result.
    if fresh:
        # INVALID-quality symbols are excluded from ranking but must not
        # run the alert pass (their signal is suppressed to HOLD); they
        # are still cached so warm scans stay pure hits.  Sprint 13.7:
        # any *suppressed* analysis (quality INVALID, reconciliation
        # conflict, unsafe provenance, stale-under-policy) is also
        # excluded from the alert batch — a suppressed signal can never
        # become a BUY/SELL alert.
        alertable = [
            (analysis["symbol"], analysis)
            for _, analysis in fresh
            if (analysis.get("data_quality") or {}).get("status") != "INVALID"
            and not analysis.get("signal_suppressed")
        ]
        batch = process_alert_batch(alertable) if alertable else {}
        for file, analysis in fresh:
            analysis["new_alerts"] = batch.get(analysis["symbol"], [])
            scanner_cache.put_analysis(file, analysis)

    ranked = rank_market(results)

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Scan complete: %d analysed, %d skipped in %.1f ms",
        len(ranked),
        len(skipped),
        elapsed_ms,
    )

    return {
        "results": ranked,
        "skipped": skipped,
    }