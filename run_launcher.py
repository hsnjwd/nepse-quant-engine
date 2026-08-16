import threading
import time

from src.logging.logger import logger
from launcher.ascii import BANNER
from launcher.config import DASHBOARD_HEALTH_TIMEOUT, DASHBOARD_PORT
from launcher.dashboard import PID_FILE, dashboard_running, show, wait_until_healthy
from launcher.process_manager import ProcessManager


def recover_dashboard(manager, timeout: float | None = None) -> tuple[bool, str]:
    """One-shot dashboard recovery after a failed boot health check.

    Clears a stale ``pids/dashboard.pid`` (it may reference a dead or
    half-started process), restarts the dashboard *once through the
    manager* — so ``dashboard_process`` stays consistent for
    ``stop_all()`` — then re-runs the health check.

    Args:
        manager: The active :class:`ProcessManager`.
        timeout: Health-check budget; ``None`` resolves the
            ``DASHBOARD_HEALTH_TIMEOUT`` config value at call time.

    Returns:
        ``(healthy, reason)`` — ``reason`` is empty on success, else the
        last health-check probe failure.
    """
    if PID_FILE.exists():
        logger.warning("Clearing stale dashboard pid file: %s", PID_FILE)
        PID_FILE.unlink(missing_ok=True)

    if dashboard_running():
        # The TCP probe only proves a port is *bound* — a half-started
        # or foreign process can hold it while /_stcore/health stays
        # down.  ``ProcessManager.start_dashboard`` would then SKIP the
        # spawn (its ``dashboard_running()`` guard), so warn explicitly:
        # the retry may not have started anything new.
        logger.warning(
            "Dashboard port %s is already bound but /_stcore/health is "
            "down — retry may skip the spawn (half-started/foreign "
            "process on the port?)",
            DASHBOARD_PORT,
        )

    manager.start_dashboard()

    return wait_until_healthy(timeout=timeout)


def start_corpus_refresher():
    """Start the daily corpus-refresh scheduler as a daemon thread.

    Runs ``scripts/refresh_corpus.py`` in-process on a schedule so the
    ``data/raw`` OHLCV corpus stays current without manual runs.  The
    first cycle runs immediately in the background; later cycles follow
    the configured daily time (``CORPUS_REFRESH_AT``).

    Disabled when ``CORPUS_REFRESH_ENABLED=0``.

    Returns:
        ``(thread, stop_event)`` when started, else ``None``.  The stop
        event lets :func:`main` end the loop cleanly on shutdown.
    """
    from launcher.config import (
        CORPUS_REFRESH_AT,
        CORPUS_REFRESH_DAYS,
        CORPUS_REFRESH_DRY_RUN,
        CORPUS_REFRESH_ENABLED,
        CORPUS_REFRESH_INTERVAL_HOURS,
    )
    from scripts import refresh_corpus

    if not CORPUS_REFRESH_ENABLED:
        logger.info(
            "Corpus refresh scheduler disabled "
            "(set CORPUS_REFRESH_ENABLED=1 to enable)"
        )
        return None

    stop_event = threading.Event()

    def _refresh_once():
        # NOTE: no ``--at-time`` here — the cadence is handled by
        # ``run_scheduler`` below; the per-cycle pass only needs the
        # scan window and the dry-run safety valve.
        argv = ["--days", str(CORPUS_REFRESH_DAYS)]
        if CORPUS_REFRESH_DRY_RUN:
            argv.append("--dry-run")
        rc = refresh_corpus.main(argv)
        if rc == 0:
            logger.info("Corpus refresh cycle completed.")
        else:
            logger.warning(
                "Corpus refresh cycle finished with exit code %s", rc
            )

    def _loop():
        refresh_corpus.run_scheduler(
            on_run=_refresh_once,
            at_time=CORPUS_REFRESH_AT,
            interval_hours=CORPUS_REFRESH_INTERVAL_HOURS,
            stop_event=stop_event,
        )

    thread = threading.Thread(
        target=_loop, name="corpus-refresher", daemon=True
    )
    thread.start()
    logger.info(
        "Corpus refresh scheduler started (at_time=%r, interval_hours=%s, "
        "dry_run=%s)",
        CORPUS_REFRESH_AT,
        CORPUS_REFRESH_INTERVAL_HOURS,
        CORPUS_REFRESH_DRY_RUN,
    )
    return thread, stop_event


def main():

    logger.info(BANNER)

    manager = ProcessManager()

    if not manager.start_all():
        logger.error("\nFailed to start NEPSE Quant Engine.")
        return

    # Dashboard boot health check.  The TCP probe in ``status()`` only
    # proves a port is bound — a half-started or foreign process can
    # hold it.  Verify the Streamlit app actually answers
    # ``/_stcore/health`` (the production gate's streamlit-boot
    # pattern).  A failure is a WARNING, never fatal: the API and bot
    # may still be healthy, and the status loop below keeps showing
    # live service state.
    healthy, reason = wait_until_healthy(timeout=DASHBOARD_HEALTH_TIMEOUT)

    if healthy:
        logger.info("✅ Streamlit Dashboard healthy (/_stcore/health 200)")
    else:
        logger.warning(
            "⚠️ Streamlit Dashboard did not answer /_stcore/health within "
            "%ss (last probe: %s) — clearing the stale pid file and "
            "retrying once...",
            DASHBOARD_HEALTH_TIMEOUT,
            reason,
        )
        healthy, reason = recover_dashboard(manager)
        if healthy:
            logger.info(
                "✅ Streamlit Dashboard recovered after restart "
                "(/_stcore/health 200)"
            )
        else:
            logger.warning(
                "⚠️ Streamlit Dashboard still unhealthy after retry "
                "(last probe: %s) — status() below still reflects the "
                "TCP port, not app health.",
                reason,
            )

    # Background corpus refresher: keeps data/raw current on a daily
    # schedule without manual runs (daemon thread — never blocks boot).
    refresher = start_corpus_refresher()

    try:

        while True:

            show(manager.status())

            time.sleep(3)

    except KeyboardInterrupt:

        logger.exception("Keyboard interrupt received")

        if refresher is not None:

            thread, stop_event = refresher

            stop_event.set()

            # Margin over the 5 s sleep-slice cap so the join rarely races
            # a just-started slice; the daemon dies at process exit anyway.
            thread.join(timeout=10)

        manager.stop_all()

        logger.info("NEPSE Quant Engine stopped.")


if __name__ == "__main__":
    main()