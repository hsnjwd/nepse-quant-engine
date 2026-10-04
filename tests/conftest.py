import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest


# Sprint 13.8 — project-local basetemp (see pytest.ini comment).  The
# default machine temp root (``pytest-of-<user>``) accumulates a broken
# ``pytest-current`` junction on Windows; pytest's dead-symlink cleanup
# then raises PermissionError at session end and a green run exits 1.
# A repo-local basetemp sidesteps that, keeps test temp state inside
# the checkout, and is wiped by pytest at every session start (the
# directory is gitignored).  Set here because ``basetemp`` is a CLI
# option, not an ini key.
def pytest_configure(config):
    config.option.basetemp = str(Path(__file__).resolve().parent.parent / ".pytest_tmp")


# Isolate all user-state modules (trade journal, user settings, alert center,
# portfolio DB, disk cache) from the real ``~/.nepse`` directory.  Must be set
# before any ``src.*`` module is imported, because the modules resolve their
# storage paths at import time.
_NEPSE_TEST_HOME = Path(tempfile.mkdtemp(prefix="nepse-test-"))
os.environ.setdefault("NEPSE_HOME", str(_NEPSE_TEST_HOME))


def _wipe_nepse_home() -> None:
    """Delete every file under the shared test home.

    Stateful modules (AlertCenter, TradeJournal, UserSettings,
    PortfolioDatabase, DiskCache) reload their JSON/SQLite state from
    ``NEPSE_HOME`` on construction.  Because the home is shared across
    the whole session, a test that saves state (e.g. an alert rule)
    would otherwise leak into every later test — producing order-
    dependent failures (the pre-Sprint 11.1 pollution cluster).
    """
    if not _NEPSE_TEST_HOME.exists():
        return
    for child in list(_NEPSE_TEST_HOME.iterdir()):
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _reset_user_state():
    """Give every test a clean user-state home.

    Wipes the shared test home before each test and resets in-memory
    singletons (UserSettings caches its loaded data on the class) so a
    test never inherits state saved by an earlier test.
    """
    _wipe_nepse_home()
    try:
        from src.config.user_settings import UserSettings

        UserSettings._instance = None
    except Exception:
        pass
    # The in-memory indicator cache (Sprint 11.4) is shared across the
    # whole session — clear it so no test inherits another test's
    # cached indicator frames (content fingerprints are deterministic,
    # so identical synthetic frames would otherwise collide across
    # tests).
    try:
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
    except Exception:
        pass
    # The notification manager is a process-wide singleton persisted to
    # ``NOTIF_FILE`` with a ``MAX_HISTORY`` cap (Sprint 13.7 finding): a
    # test that fills it to the cap would leave later tests with
    # ``unread_count`` already at 500, so ``notify()`` can no longer
    # grow it.  Reset the singleton AND rebind the module-level alias
    # (``notification_manager``, imported directly by UI pages) so no
    # test inherits another test's notification state.
    try:
        import src.ui.notifications as notif_module

        notif_module.NotificationManager._instance = None
        notif_module.notification_manager = notif_module.NotificationManager()
    except Exception:
        pass
    yield


@pytest.fixture
def nepse_home() -> Path:
    """Return the isolated user-state root used by the test session."""
    return _NEPSE_TEST_HOME


@pytest.fixture(scope="session")
def ci_gate_artifact(tmp_path_factory):
    """Run the full CI benchmark gate ONCE per session and share its artifact.

    Sprint 12.0 Phase 11: the gate previously ran in full three times in
    the test suite (``test_sprint11_8`` x1, ``test_sprint11_9`` x2),
    each a ~2-4 s full measurement.  The measured ratios are
    machine-deterministic for the same synthetic corpus, so a single
    session-scoped run preserves every assertion while cutting the
    wall-clock overhead.  The FAIL-path test derives its verdict from
    the same real measurement (the gate's check arithmetic is pure), so
    gate behavior is not weakened — only the repeated measurement is
    removed.
    """
    from benchmarks import ci_gate

    out = tmp_path_factory.mktemp("ci_gate") / "benchmark-ci.json"
    _passed, artifact = ci_gate.run_gate(out_file=out)
    return artifact


@pytest.fixture
def sample_frame():
    return pd.DataFrame(
        [
            {
                "Close": 100.0,
                "SMA_20": 95.0,
                "SMA_50": 90.0,
                "RSI": 55.0,
                "MACD": 1.5,
                "MACD_SIGNAL": 0.8,
                "VOLUME_SIGNAL": "NORMAL",
                "RELATIVE_VOLUME": 1.2,
                "VOLUME_SCORE": 1,
                "ATR": 2.5,
            }
        ]
    )


@pytest.fixture
def sample_breakdown():
    return {
        "trend": 1,
        "rsi": 1,
        "macd": 2,
        "volume": 1,
        "pattern": 3,
        "reasons": ["healthy"],
    }


@pytest.fixture
def base_result():
    return {
        "signal": "BUY",
        "score": 5,
        "confidence": 90,
        "trend": "UPTREND",
        "volume_signal": "VOLUME_SPIKE",
        "relative_volume": 1.8,
        "pattern_type": "Bullish",
        "pattern": "Bullish Engulfing",
        "price": 100.0,
        "target1": 110.0,
        "target2": 120.0,
        "target3": 130.0,
        "milestones": {
            "target1": False,
            "target2": False,
            "target3": False,
        },
        "best_rr": 3.5,
    }
