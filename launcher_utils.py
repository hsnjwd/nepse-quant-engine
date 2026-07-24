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

def start_api():

    log("Starting API...")

    api_log = open(API_LOG, "w")

    process = subprocess.Popen(

        [
            get_python_executable(),
            "-m",
            "uvicorn",
            "src.api.main:app",
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