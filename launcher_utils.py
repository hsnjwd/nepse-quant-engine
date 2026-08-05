import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from src.logging.logger import logger

# ----------------------------------------------------
# Project Paths
# ----------------------------------------------------

ROOT = Path(__file__).parent

LOG_DIR = ROOT / "logs"
PID_DIR = ROOT / "pids"

API_LOG = LOG_DIR / "api.log"
BOT_LOG = LOG_DIR / "bot.log"
LAUNCHER_LOG = LOG_DIR / "launcher.log"

API_PID = PID_DIR / "api.pid"
BOT_PID = PID_DIR / "bot.pid"

API_URL = "http://127.0.0.1:8000/"


# ----------------------------------------------------
# Ensure folders exist
# ----------------------------------------------------

LOG_DIR.mkdir(exist_ok=True)
PID_DIR.mkdir(exist_ok=True)


# ----------------------------------------------------
# Logging
# ----------------------------------------------------

def get_python_executable():
    """Return a Python executable that works reliably on Windows."""
    candidates = []

    if sys.executable:
        candidates.append(sys.executable)

    for name in ("python", "py", "python3"):
        candidate = shutil.which(name)
        if candidate:
            candidates.append(candidate)

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    return "python"


def log(message):

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    line = f"[{timestamp}] {message}"

    logger.info(line)

    with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ----------------------------------------------------
# PID Helpers
# ----------------------------------------------------

def save_pid(path, pid):

    with open(path, "w") as f:
        f.write(str(pid))


def load_pid(path):

    if not path.exists():
        return None

    return int(path.read_text().strip())


# ----------------------------------------------------
# API Health Check
# ----------------------------------------------------

def wait_for_api(timeout=60):

    log("Waiting for API...")

    start = time.time()

    while True:

        try:

            response = requests.get(API_URL, timeout=2)

            if response.status_code == 200:

                log("API is online.")

                return True

        except Exception:
            logger.exception("API health check failed")

        if time.time() - start > timeout:

            log("API startup timed out.")

            return False

        time.sleep(1)


# ----------------------------------------------------
# Start API
# ----------------------------------------------------

def _check_fastapi_compat():
    """Warn early when the installed fastapi/starlette pair is known-broken.

    fastapi 0.115.0-0.115.5 crashes at import time with Starlette >= 0.40
    (``TypeError: Router.__init__() got an unexpected keyword argument
    'on_startup'``), which keeps the API down and the dashboard in demo
    mode.  requirements.txt pins ``fastapi>=0.116.0`` for this reason.
    """
    try:
        from importlib.metadata import version

        fv = version("fastapi")
        sv = version("starlette")

        def _v(s: str) -> tuple:
            return tuple(int(p) for p in s.split(".")[:2] if p.isdigit())

        fast_ok = _v(fv) >= (0, 116)
        starlet_ok = _v(sv) < (0, 40)

        if not fast_ok and not starlet_ok:
            log(
                "WARNING: fastapi " + fv + " with starlette " + sv
                + " is the known-broken pair (Router on_startup crash). "
                + "Run: .venv\\Scripts\\python.exe -m pip install -U "
                + "\"fastapi>=0.116.0\""
            )
    except Exception:
        # Never block startup on the version pre-flight.
        pass


def start_api():

    _check_fastapi_compat()

    log("Starting API...")

    # The static web UI (ui/index_standalone.html) is served from a
    # different origin than the API.  Open CORS by default unless the
    # user already chose a specific allow-list, otherwise the browser
    # blocks the fetch calls and the dashboard stays in demo mode.
    os.environ.setdefault("CORS_ORIGINS", "*")

    api_log = open(API_LOG, "w")

    process = subprocess.Popen(

        [
            get_python_executable(),
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--reload",
        ],

        stdout=api_log,

        stderr=subprocess.STDOUT,

    )

    save_pid(API_PID, process.pid)

    log(f"API PID = {process.pid}")

    return process


# ----------------------------------------------------
# Start Telegram Bot
# ----------------------------------------------------

def start_bot():

    log("Starting Telegram Bot...")

    bot_log = open(BOT_LOG, "w")

    process = subprocess.Popen(

        [
            get_python_executable(),
            "-m",
            "src.bot.telegram_bot",
        ],

        stdout=bot_log,

        stderr=subprocess.STDOUT,

    )

    save_pid(BOT_PID, process.pid)

    log(f"Bot PID = {process.pid}")

    return process


# ----------------------------------------------------
# Stop Process
# ----------------------------------------------------

def stop_process(pid_file, name):

    pid = load_pid(pid_file)

    if pid is None:

        log(f"{name} not running.")

        return

    try:

        subprocess.run(

            ["taskkill", "/PID", str(pid), "/F"],

            stdout=subprocess.DEVNULL,

            stderr=subprocess.DEVNULL,

        )

        log(f"{name} stopped.")

    except Exception:

        logger.exception("Failed to stop %s", name)

    try:

        os.remove(pid_file)

    except Exception:

        pass


# ----------------------------------------------------
# Status
# ----------------------------------------------------

def print_banner():

    logger.info("")

    logger.info("=" * 45)

    logger.info("      NEPSE QUANT ENGINE")

    logger.info("=" * 45)

    logger.info("")