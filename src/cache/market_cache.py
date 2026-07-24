import threading
import time

from src.logging.logger import logger
from src.scanner.engine import scan_market

CACHE = None
LAST_SCAN = 0

CACHE_SECONDS = 300

LOCK = threading.Lock()


def get_market_scan():

    global CACHE
    global LAST_SCAN

    now = time.time()

    if CACHE is not None and (now - LAST_SCAN) < CACHE_SECONDS:
        return CACHE

    with LOCK:

        now = time.time()

        if CACHE is not None and (now - LAST_SCAN) < CACHE_SECONDS:
            return CACHE

        logger.info("Refreshing market cache...")

        CACHE = scan_market()

        LAST_SCAN = now

        return CACHE