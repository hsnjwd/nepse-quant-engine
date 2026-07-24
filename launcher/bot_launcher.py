import subprocess
import sys
from pathlib import Path

from src.logging.logger import logger

PID_FILE = Path("pids/bot.pid")


def start_bot():
    """
    Starts the Telegram bot in the background.
    Returns the subprocess object.
    """

    logger.info("🤖 Starting Telegram Bot...")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "src.bot.telegram_bot",
        ]
    )

    PID_FILE.parent.mkdir(exist_ok=True)

    PID_FILE.write_text(str(process.pid))

    return process


def stop_bot(process=None):
    """
    Stops the Telegram bot.
    """

    if process is not None:
        process.terminate()
        process.wait()

    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)