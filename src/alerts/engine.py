import copy
import threading

from src.alerts.history import (
    HISTORY_FILE,
    get_last_state,
    load_history,
    save_history,
    update_state,
)
from src.logging.logger import logger
from src.utils.json_store import locked_json

# Guards the read-modify-write of alert state.  The parallel scanner
# analyses many symbols concurrently (ThreadPoolExecutor); without this
# lock two workers could read + write ``data/alerts/history.json`` at
# the same time and lose updates.  Sprint 11.7 adds the cross-process
# file lock (``locked_json``) on top of this in-process RLock so two
# uvicorn workers (separate OS processes) cannot lose each other's
# alert-history updates either.
_ALERT_STATE_LOCK = threading.RLock()

from src.alerts.rules import (
    check_signal_alerts,
    check_confidence_alerts,
    check_score_alerts,
    check_trend_alerts,
    check_volume_alerts,
    check_target_alerts,
)


def process_alerts(symbol, result):
    """
    Generate only NEW alerts.

    Thread-safe: the whole read-check-update cycle runs under a
    module-level lock so concurrent scanner workers never corrupt the
    shared alert history file.

    Performance (Sprint 11.2): the history file is read exactly once
    per symbol and threaded through the check/update helpers, instead of
    ``get_last_state`` + ``update_state`` each re-reading the whole file
    (2 reads + 1 write per symbol, serialised by the lock).  Profiling a
    60-symbol scan showed 120 full-file reads dominating ``process_alerts``
    (~3.5 s); this halves the reads and keeps the write-once semantics.
    """

    with _ALERT_STATE_LOCK, locked_json(HISTORY_FILE):
        history = load_history()
        previous = get_last_state(symbol, history=history)

        return _process_alerts_locked(symbol, result, previous, history)


def _process_alerts_locked(symbol, result, previous, history=None, save=True):
    """Compute new alerts under the state lock (no lock acquisition).

    Args:
        symbol: Stock symbol being processed.
        result: Current analysis result.
        previous: Previous stored state for *symbol* (or ``None``).
        history: Preloaded history dict (shared across the lock-held
            cycle) so ``update_state`` does not re-read the file.
        save: When ``False``, defer the history write to the caller
            (used by ``process_alert_batch`` for a single atomic write
            per scan).
    """

    # Sprint 13.7 alert safety: an analysis whose signal was suppressed
    # (invalid data, reconciliation conflict, unsafe provenance, stale
    # under enforced policy) must never become a BUY/SELL alert.  The
    # history state is still recorded (so a later recovery compares
    # against the suppressed HOLD state), but the only emitted alert is
    # an informational SUPPRESSED note carrying the reason.
    if result.get("signal_suppressed"):
        reason = result.get("signal_suppression_reason") or "unsafe_data"
        update_state(symbol, result, history=history, save=save)
        return [
            {
                "type": "SUPPRESSED",
                "priority": 1,
                "message": f"Signal suppressed: {reason}",
            }
        ]

    alerts = []

    # First scan
    if previous is None:

        update_state(symbol, result, history=history, save=save)

        return [
            {
                "type": "INITIAL",
                "priority": 1,
                "message": (
                    f"Initial signal: {result['signal']}"
                )
            }
        ]

    alerts.extend(
        check_signal_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_confidence_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_score_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_trend_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_volume_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_target_alerts(
            previous,
            result
        )
    )

    update_state(symbol, result, history=history, save=save)

    return alerts


def process_alert_batch(entries):
    """Process alerts for many symbols with ONE history read + ONE write.

    Sprint 11.3 scan-level batching: ``process_alerts`` re-reads and
    re-serialises the whole history file per symbol (O(n²) JSON work in
    a scan, measured at ~9 s for 50 symbols).  This API loads the
    history once, mutates the in-memory dict for every entry under the
    same module lock, and persists once atomically at the end.

    Args:
        entries: Iterable of ``(symbol, result)`` tuples.  The scanner
            collects its fresh analyses and hands them here — it never
            touches the persistence layer itself.

    Returns:
        Mapping of symbol -> list of new alerts (same shape as the
        single-symbol ``process_alerts`` return).

    Failure isolation: a malformed entry (e.g. missing keys in one
    result) is logged and skipped without aborting the batch, and the
    partially-written in-memory state is rolled back so a single bad
    symbol can never corrupt the shared history.
    """
    entries = list(entries)
    if not entries:
        return {}

    with _ALERT_STATE_LOCK, locked_json(HISTORY_FILE):
        history = load_history()
        alerts_by_symbol: dict[str, list[dict]] = {}
        for symbol, result in entries:
            # Deep snapshot so a failure mid-update can be rolled back:
            # ``get_last_state``/``update_state`` mutate the stored dict
            # in place, so a reference would not undo partial writes for
            # symbols that already had state.
            prev_snapshot = copy.deepcopy(history[symbol]) if symbol in history else None
            try:
                previous = get_last_state(symbol, history=history)
                alerts_by_symbol[symbol] = _process_alerts_locked(
                    symbol, result, previous, history, save=False
                )
            except Exception as exc:  # noqa: BLE001 - per-entry isolation
                logger.exception("Alert batch failed for %s: %s", symbol, exc)
                if prev_snapshot is None:
                    history.pop(symbol, None)
                else:
                    history[symbol] = prev_snapshot
                alerts_by_symbol[symbol] = []
        save_history(history)
        return alerts_by_symbol