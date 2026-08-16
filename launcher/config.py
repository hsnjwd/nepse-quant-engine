import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOGS = ROOT / "logs"
PIDS = ROOT / "pids"

API_PORT = 8000

# Streamlit dashboard (``app.py``) — the default Streamlit port.
DASHBOARD_PORT = 8501

# How long run_launcher polls the dashboard's /_stcore/health endpoint
# after boot before declaring it unhealthy (seconds).
DASHBOARD_HEALTH_TIMEOUT = 90

REFRESH_SECONDS = 5

# ---------------------------------------------------------------------------
# Corpus refresh scheduler (scripts/refresh_corpus.py)
#
# The launcher starts a daemon thread that fetches the freshest yonepse
# daily shard and appends new OHLCV rows to data/raw/*.csv on a daily
# schedule, so the corpus stays current without manual runs.  All knobs
# are env-overridable so operators can disable it or tune the cadence
# without editing code.
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: str) -> bool:
    """Parse an env var as a boolean ('' / '0' / 'false' / 'no' -> False)."""
    return os.getenv(name, default).strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


def _env_int(name: str, default: int) -> int:
    """Parse an env var as an int, falling back to *default* on garbage.

    A typo'd value must never crash the whole launcher at import.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Parse an env var as a float, falling back to *default* on garbage."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


# Master switch — ON by default (the point of this feature is unattended
# freshness); set CORPUS_REFRESH_ENABLED=0 to turn it off.
CORPUS_REFRESH_ENABLED = _env_bool("CORPUS_REFRESH_ENABLED", "1")

# Daily wall-clock time for the refresh (local time; NEPSE closes ~15:15
# NPT, so 16:00 is a safe post-close default).  Empty disables the
# time-of-day cadence in favour of the pure interval below.
CORPUS_REFRESH_AT = os.getenv("CORPUS_REFRESH_AT", "16:00")

# Fallback cadence (hours) when --at-time is empty/invalid.  Parsed
# defensively: a non-numeric env value falls back to the default instead
# of crashing the launcher at import.
CORPUS_REFRESH_INTERVAL_HOURS = _env_float(
    "CORPUS_REFRESH_INTERVAL_HOURS", 24.0
)

# How far back the freshest-shard scan probes for a daily shard (>= 1).
CORPUS_REFRESH_DAYS = max(_env_int("CORPUS_REFRESH_DAYS", 14), 1)

# Safety valve: when set, the refresher only reports what it would change.
CORPUS_REFRESH_DRY_RUN = _env_bool("CORPUS_REFRESH_DRY_RUN", "0")

LOGS.mkdir(exist_ok=True)
PIDS.mkdir(exist_ok=True)