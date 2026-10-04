"""Core technical stock analysis engine."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from src.alerts.engine import process_alert_batch, process_alerts
from src.alerts.rules import check_alerts
from src.decision.confidence import calculate_confidence
from src.decision.signal_engine import generate_signal
from src.indicators.cache import indicator_cache
from src.indicators.momentum import add_momentum_indicators
from src.indicators.moving_average import add_moving_averages
from src.indicators.volatility import add_volatility_indicators
from src.indicators.volume import add_volume_indicators
from src.loaders.csv_loader import load_csv
from src.logging.logger import logger
from src.market_structure.levels import get_resistance, get_support, get_trend
from src.patterns.candlestick import detect_pattern
from src.recommendations.trade_plan import create_trade_plan
from src.risk.position_size import calculate_position_size
from src.risk.reward import calculate_risk_reward
from src.signals.scorer import calculate_score


def safe_float(value: Any) -> float | None:
    """Convert a value to a float while preserving missing values.

    Args:
        value: Value to convert.

    Returns:
        The converted float, or ``None`` when the value is missing.
    """
    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    return float(value)


_OHLCV_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}


def _is_cacheable_frame(df: Any) -> bool:
    """True when *df* can be fingerprinted for the indicator cache.

    Only raw OHLCV frames — the pipeline's input shape — are cached.
    Frames missing the required columns or that are empty fall through
    to the normal pipeline untouched, preserving existing behaviour for
    every non-OHLCV caller (e.g. the 1-row test fixture, already-
    augmented frames, uploaded-history subsets).
    """
    return (
        isinstance(df, pd.DataFrame)
        and not df.empty
        and _OHLCV_COLUMNS.issubset(df.columns)
    )


def analyze_dataframe(
    df: Any,
    symbol: str | None = None,
    use_cache: bool = True,
    quality_check: bool = True,
    reconciliation: Any = None,
    provenance: Any = None,
) -> dict[str, Any]:
    """Analyze an OHLCV data frame and return technical analysis results.

    Args:
        df: Price data containing the required OHLCV columns.
        symbol: Optional symbol namespace for the indicator-cache key
            (Sprint 11.4).  Prevents coincidentally-identical prices
            for different symbols from sharing a cache entry.
        use_cache: When ``True`` (default), consult the fingerprint-
            based indicator cache.  Workloads that analyse a
            continuously-growing frame — e.g. the backtest's expanding
            window, where the same frame is never analysed twice —
            should pass ``False`` so the fingerprint cost is pure
            overhead.
        quality_check: When ``True`` (default), the frame is assessed
            against the canonical market-data contract (Sprint 13.3)
            and the ``data_quality`` block is attached.  Invalid data
            always suppresses the signal; stale data suppresses it only
            when ``ENFORCE_SIGNAL_FRESHNESS`` is enabled.  Backtest
            loops pass ``False`` for expanding windows (the same frame
            is re-analysed every step and its quality is checked once
            by the caller).
        reconciliation: Optional ``ReconciliationResult`` (Sprint 13.4).
            When provided, the additive ``reconciliation`` block is
            attached and an unresolved MATERIAL_DISAGREEMENT /
            MAPPING_CONFLICT suppresses the signal to HOLD — a provider
            conflict must never silently become a normal BUY/SELL.
        provenance: Optional provenance/trust state (Sprint 13.5).
            Accepts a ``DataProvenance`` or its ``to_dict()`` mapping.
            When provided, the additive ``provenance`` block is attached
            and an **unsafe** trust state (conflicted / unavailable /
            calendar-invalid / quarantined) suppresses the signal to
            HOLD — a dataset whose provenance is explicitly unsafe must
            never silently become a normal BUY/SELL, even if its quality
            check happened to pass.

    Returns:
        Analysis values for the most recent candle, including additive
        ``data_quality``, ``reconciliation`` and ``provenance`` blocks.
    """
    quality = None
    # Quality assessment runs only on OHLCV-shaped frames — the same gate
    # the indicator cache uses.  Indicator-augmented frames (e.g. the
    # 1-row fixture with precomputed SMA/RSI columns) are not raw market
    # data and carry no market-data contract to check, so they pass
    # through byte-identically (Sprint 13.3).
    if quality_check and _is_cacheable_frame(df):
        from src.data.quality import assess_history, quality_metrics  # noqa: PLC0415

        quality = assess_history(df, symbol=symbol, source="analysis")
        quality_metrics.record_report(quality)

    cache_key: str | None = None
    if use_cache and _is_cacheable_frame(df):
        cache_key = indicator_cache.build_key(df, symbol=symbol)
        cached = indicator_cache.get(cache_key)
        if cached is not None:
            # Cache hit: *cached* is a private copy (Sprint 11.4
            # copy-on-return), so the indicator chain is skipped and the
            # cheap analysis tail is re-derived from it.
            result = _build_analysis(cached)
            return _finalize(result, quality, reconciliation, provenance)

    # Single defensive copy: all indicator steps below run in-place on
    # this frame, so callers (and the scanner DataFrame cache) never
    # have their frames mutated.  Previously every ``add_*`` step
    # copied the frame (8 copies per stock); now it is exactly one.
    df = df.copy()
    df = add_moving_averages(df, inplace=True)
    df = add_momentum_indicators(df, inplace=True)
    df = add_volume_indicators(df, inplace=True)
    df = add_volatility_indicators(df, inplace=True)

    if df.empty:
        raise ValueError("No rows remaining after indicator calculation.")

    if cache_key is not None:
        indicator_cache.put(cache_key, df)

    result = _build_analysis(df)
    return _finalize(result, quality, reconciliation, provenance)


def _attach_data_quality(
    result: dict[str, Any],
    quality: Any,
    reconciliation: Any = None,
    provenance: Any = None,
) -> dict[str, Any]:
    """Attach the additive blocks and enforce signal safety.

    Sprint 13.3 Phase 14: invalid/stale/conflicting market data must not
    silently generate a normal trading signal.

    - INVALID data  -> signal forced to HOLD, ``signal_suppressed=True``
    - STALE data    -> signal forced to HOLD only when
      ``ENFORCE_SIGNAL_FRESHNESS`` is enabled; always flagged in the
      ``data_quality`` block otherwise
    - VALID data    -> unchanged

    Sprint 13.4 (Phase 17): when a ``ReconciliationResult`` is attached,
    an unresolved MATERIAL_DISAGREEMENT or MAPPING_CONFLICT suppresses
    the signal to HOLD (``signal_suppression_reason`` reflects the
    reconciliation state) — a provider conflict never silently becomes
    a normal trading signal.  A MINOR disagreement is documented via
    the ``reconciliation`` block and does not suppress.

    Sprint 13.5: when a provenance/trust state is attached, an **unsafe**
    trust (conflicted / unavailable / calendar-invalid / quarantined)
    suppresses the signal to HOLD with ``signal_suppression_reason`` =
    the trust state — provenance safety is applied *last* so it wins
    over every other block (a dataset whose provenance is explicitly
    unsafe must never become a normal BUY/SELL).

    All blocks are purely additive; callers that never opt into them
    receive their exact legacy response shape.
    """
    if quality is not None:
        result["data_quality"] = quality.to_dict()

    if quality is None and reconciliation is None and provenance is None:
        return result

    # 1. Data-quality suppression first (INVALID always suppresses;
    #    STALE suppresses only when enforced).
    if quality is not None:
        if quality.status == "INVALID":
            result["signal"] = "HOLD"
            result["signal_suppressed"] = True
            result["signal_suppression_reason"] = "invalid_data"
        elif quality.status == "SUSPICIOUS" and quality.freshness == "STALE":
            from src.config import ENFORCE_SIGNAL_FRESHNESS  # noqa: PLC0415

            if ENFORCE_SIGNAL_FRESHNESS:
                result["signal"] = "HOLD"
                result["signal_suppressed"] = True
                result["signal_suppression_reason"] = "stale_data"
            else:
                result["signal_suppressed"] = False
                result["signal_suppression_reason"] = None
        else:
            result["signal_suppressed"] = False
            result["signal_suppression_reason"] = None

    # 2. Reconciliation suppression next (a provider conflict wins over
    #    an otherwise VALID frame).
    if reconciliation is not None:
        block = (
            reconciliation.to_dict()
            if hasattr(reconciliation, "to_dict")
            else dict(reconciliation)
        )
        result["reconciliation"] = block
        rec_status = block.get("status")
        if rec_status in ("MATERIAL_DISAGREEMENT", "MAPPING_CONFLICT"):
            result["signal"] = "HOLD"
            result["signal_suppressed"] = True
            result["signal_suppression_reason"] = (
                "material_disagreement"
                if rec_status == "MATERIAL_DISAGREEMENT"
                else "mapping_conflict"
            )

    # 3. Provenance suppression *last* — the most authoritative safety
    #    gate: an explicitly-unsafe provenance always wins.
    if provenance is not None:
        prov_block = (
            provenance.to_dict()
            if hasattr(provenance, "to_dict")
            else dict(provenance)
        )
        result["provenance"] = prov_block
        trust = prov_block.get("trust")
        unsafe = prov_block.get("is_safe") is False or trust in (
            "conflicted",
            "unavailable",
            "calendar_invalid",
            "quarantined",
        )
        if unsafe:
            result["signal"] = "HOLD"
            result["signal_suppressed"] = True
            result["signal_suppression_reason"] = trust or "unsafe_provenance"

    # Sprint 13.7 alert safety: a suppressed signal must never carry a
    # BUY/SELL rule alert.  ``check_alerts`` ran inside ``_build_analysis``
    # against the *raw* pre-suppression signal, so a quarantined /
    # conflicted / calendar-invalid / unavailable symbol whose raw signal
    # was BUY would otherwise ship a BUY alert alongside a HOLD signal.
    # Directional rule alerts are scrubbed and the suppression reason is
    # preserved as an informational SUPPRESSED note.
    if result.get("signal_suppressed"):
        reason = result.get("signal_suppression_reason")
        result["alerts"] = [
            a for a in result.get("alerts", [])
            if a.get("type") not in ("BUY", "SELL")
        ]
        if reason:
            result["alerts"].append({
                "type": "SUPPRESSED",
                "priority": 1,
                "message": f"Signal suppressed: {reason}",
            })
    return result


def _finalize(
    result: dict[str, Any],
    quality: Any,
    reconciliation: Any = None,
    provenance: Any = None,
) -> dict[str, Any]:
    """Attach the safety blocks, then auto-derive additive provenance.

    Sprint 13.7 scanner trust integration: when no explicit provenance
    was supplied but a quality report exists (the scanner / API / CSV
    paths), a compact provenance block is derived through the *existing*
    trust resolver (``provenance_from_history``) — no duplicate
    trust-resolution logic in the scanner.  The block is purely additive
    metadata (the quality block already decided INVALID/STALE
    suppression) and uses the deterministic ``quality.as_of`` so cache
    hit == cache miss.
    """
    result = _attach_data_quality(result, quality, reconciliation, provenance)
    if provenance is None and quality is not None and "provenance" not in result:
        try:
            from src.data.provenance import provenance_from_history  # noqa: PLC0415 - lazy

            as_of = (
                getattr(quality, "as_of", None)
                or getattr(quality, "latest_date", None)
                or "unknown"
            )
            prov = provenance_from_history(
                sources=["csv"],
                quality=quality,
                as_of=as_of,
            )
            result["provenance"] = prov.to_dict()
        except Exception:  # noqa: BLE001 - additive metadata never breaks analysis
            pass
    return result


def _build_analysis(df: pd.DataFrame) -> dict[str, Any]:
    """Derive the analysis dict from an indicator-augmented frame."""
    latest = df.iloc[-1]

    support = get_support(df)
    resistance = get_resistance(df)
    trend = get_trend(latest)
    pattern = detect_pattern(df)

    score, breakdown = calculate_score(latest, pattern)
    confidence = calculate_confidence(score, breakdown)

    result = {
        "price": safe_float(latest["Close"]),
        "score": score,
        "confidence": confidence,
        "rsi": safe_float(latest["RSI"]),
        "macd": safe_float(latest["MACD"]),
        "atr": safe_float(latest["ATR"]),
        "trend": trend,
        "support": support,
        "resistance": resistance,
        "pattern": pattern["name"],
        "pattern_type": pattern["type"],
        "pattern_strength": pattern["strength"],
        "pattern_score": pattern["score"],
        "volume_signal": latest["VOLUME_SIGNAL"],
        "relative_volume": safe_float(latest["RELATIVE_VOLUME"]),
        "volume_score": int(latest["VOLUME_SCORE"]),
        "score_breakdown": breakdown,
    }

    result["signal"] = generate_signal(result)

    # Generate trade plan
    result.update(create_trade_plan(result))

    # Calculate Risk/Reward
    result.update(calculate_risk_reward(result))

    # Calculate Position Size
    result.update(calculate_position_size(result))

    # Generate Alerts
    alerts = check_alerts(result)
    result["alerts"] = alerts
    return result


def analyze_stock(file: str, with_alerts: bool = True) -> dict[str, Any]:
    """Load and analyze a stock CSV file.

    Args:
        file: Path to the source CSV file.
        with_alerts: When True (default, single-symbol callers like
            the API and portfolio), run the per-symbol alert engine.
            The scanner passes False and instead batches alert
            processing once per scan (Sprint 11.3) so the history file
            is read and written once instead of once per symbol.

    Returns:
        Analysis values enriched with the stock symbol and new alerts.

    Raises:
        ValueError: If the CSV has no usable data.
    """
    df = load_csv(file)

    if df.empty:
        raise ValueError("CSV contains no usable data.")

    result = analyze_dataframe(df, symbol=Path(file).stem.upper())
    result["symbol"] = Path(file).stem.upper()
    if with_alerts:
        result["new_alerts"] = process_alerts(result["symbol"], result)
    else:
        # Shape consistency: the scanner attaches the scan-level batch
        # results after collection (Sprint 11.3).
        result["new_alerts"] = []
    return result


def analyze_stock_batch(
    files: list[str],
    use_batch_alerts: bool | None = None,
) -> list[dict]:
    """Analyze many CSV files, batching alert state into one read + write.

    Sprint 11.9 (Phase 5): the smallest internal multi-analysis
    abstraction for API paths that analyze several symbols together
    (watchlist scan, portfolio).  The single-symbol ``/api/analyze``
    endpoint is *not* a batch and is intentionally left on
    ``analyze_stock``.

    Args:
        files: CSV paths to analyze.
        use_batch_alerts: When ``None`` (default), honours the
            ``ENABLE_ANALYZE_ALERT_BATCH`` config flag (off by default).
            When ``True``, processes alert state through
            ``process_alert_batch`` (one history read + one atomic
            write for the whole batch).  When ``False``, runs the
            legacy per-symbol ``process_alerts`` engine — identical to
            a plain loop of ``analyze_stock(..., with_alerts=True)``.

    Returns:
        A list of analysis dicts in *files* order, each with
        ``symbol`` and ``new_alerts`` attached.  A file that raises is
        logged and returned as an error entry (per-symbol isolation,
        matching the scanner's contract) so one bad symbol never
        aborts the batch.
    """
    if use_batch_alerts is None:
        from src.config import ENABLE_ANALYZE_ALERT_BATCH  # noqa: PLC0415

        use_batch_alerts = ENABLE_ANALYZE_ALERT_BATCH

    results: list[dict] = []
    pending: list[tuple[str, dict]] = []
    for file in files:
        symbol = Path(file).stem.upper()
        try:
            if use_batch_alerts:
                result = analyze_stock(file, with_alerts=False)
                pending.append((symbol, result))
            else:
                result = analyze_stock(file, with_alerts=True)
            result["symbol"] = symbol
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - per-symbol isolation
            logger.warning("analyze_stock_batch failed for %s: %s", symbol, exc)
            results.append({"symbol": symbol, "error": str(exc)})

    if use_batch_alerts and pending:
        batch = process_alert_batch(pending)
        for result in results:
            if result.get("error") is None and result["symbol"] in batch:
                result["new_alerts"] = batch[result["symbol"]]

    return results