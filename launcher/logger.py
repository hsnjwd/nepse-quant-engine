from pathlib import Path
from datetime import datetime

from launcher.config import LOGS
from src.logging.logger import logger

LOGFILE = LOGS / "launcher.log"


def log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")

    line = f"[{timestamp}] {message}"

    logger.info(line)

    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")