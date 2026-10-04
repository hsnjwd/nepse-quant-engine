import threading
import time

from src.config import SCANNER_CACHE_TTL
from src.logging.logger import logger
from src.scanner.engine import scan_market

CACHE = None
LAST_SCAN = 0

# Backward-compatible alias for the scanner cache TTL (Sprint 11:
# the constant moved to configuration so operators can tune it).
CACHE_SECONDS = SCANNER_CACHE_TTL

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

        logger.info("Refreshing market cache (TTL=%ss)...", CACHE_SECONDS)

        CACHE = scan_market()

        LAST_SCAN = now

        return CACHE