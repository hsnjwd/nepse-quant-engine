import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from src.logging.logger import logger

from launcher.config import DASHBOARD_HEALTH_TIMEOUT, DASHBOARD_PORT, ROOT

# Poll interval for the boot health check (seconds).  Kept as a module
# constant so tests can shorten it without touching production timing.
_HEALTH_POLL_SECONDS = 1.0

HOST = "127.0.0.1"

PID_FILE = Path("pids/dashboard.pid")


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def show(status):

    clear()

    logger.info("=" * 60)
    logger.info("              NEPSE QUANT ENGINE")
    logger.info("=" * 60)
    logger.info("")
    logger.info("         Professional Trading Platform")
    logger.info("")
    logger.info("           NEPSE Quant Engine v1.0")
    logger.info("           Built by Jawed Hassan")
    logger.info("                 © 2026")
    logger.info("")
    logger.info("=" * 60)
    logger.info("")

    logger.info(
        f"API Server      {'🟢 Running' if status['api'] else '🔴 Stopped'}"
    )

    logger.info(
        f"Telegram Bot    {'🟢 Running' if status['bot'] else '🔴 Stopped'}"
    )

    logger.info(
        f"Streamlit Dash  {'🟢 Running' if status.get('dashboard', False) else '🔴 Stopped'}"
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Press Ctrl+C to Exit")
    logger.info("")

    logger.info(
        f"Last Refresh: {time.strftime('%H:%M:%S')}"
    )


# ---------------------------------------------------------------------------
# Streamlit dashboard process management (pid-file managed like the API and
# bot launchers: ``pids/dashboard.pid`` is written on start and unlinked on
# stop, so ``status()`` can reflect every service the launcher owns).
# ---------------------------------------------------------------------------


def dashboard_running():
    """
    Returns True if the Streamlit dashboard is answering on DASHBOARD_PORT.
    """
    sock = socket.socket()
    sock.settimeout(1)

    try:
        sock.connect((HOST, DASHBOARD_PORT))
        return True
    except Exception:
        # Debug level: ``ProcessManager.status()`` probes this every
        # refresh (3 s in run_launcher), so a stopped dashboard must not
        # spam a full traceback per poll (deliberate deviation from
        # ``api_running``'s noisier ``logger.exception`` precedent).
        logger.debug("Dashboard health check failed", exc_info=True)
        return False
    finally:
        sock.close()


def start_dashboard():
    """
    Starts the Streamlit dashboard (``app.py``) in the background.
    Returns the subprocess object.
    """

    if dashboard_running():
        logger.info("✅ Dashboard already running.")
        return None

    logger.info("🚀 Starting Streamlit Dashboard...")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app.py"),
            "--server.address=127.0.0.1",
            "--server.port",
            str(DASHBOARD_PORT),
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    PID_FILE.parent.mkdir(exist_ok=True)

    PID_FILE.write_text(str(process.pid))

    return process


def wait_until_healthy(timeout: float | None = None) -> tuple[bool, str]:
    """Poll Streamlit's ``/_stcore/health`` endpoint until it answers 200.

    Mirrors the production gate's streamlit-boot pattern
    (``benchmarks.production_gate.section_streamlit``): the TCP probe in
    ``dashboard_running()`` only proves a port is *bound* — a port can be
    bound by a half-started or foreign process — whereas this verifies
    the Streamlit app itself is up.

    Args:
        timeout: Total wall-clock budget in seconds.  ``None`` falls back
            to the ``DASHBOARD_HEALTH_TIMEOUT`` config value, resolved at
            call time (not bound at import).

    Returns:
        ``(True, "")`` when the health endpoint answered 200, otherwise
        ``(False, last_reason)`` where ``last_reason`` is the most recent
        probe failure (or ``"not attempted"``).
    """
    import urllib.request  # noqa: PLC0415

    if timeout is None:
        timeout = DASHBOARD_HEALTH_TIMEOUT

    deadline = time.monotonic() + timeout
    last = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310 - loopback launcher probe
                f"http://{HOST}:{DASHBOARD_PORT}/_stcore/health",
                timeout=5.0,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                last = f"status={resp.status}"
        except Exception as exc:  # noqa: BLE001 - startup probing
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(_HEALTH_POLL_SECONDS)
    return False, last


def stop_dashboard(process=None):
    """
    Stops the Streamlit dashboard.
    """

    if process is not None:
        process.terminate()
        process.wait()

    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)