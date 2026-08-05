"""Market replay engine — replay historical NEPSE trading sessions.

Provides play/pause/step/fast-forward/rewind controls and injects
historical data through the DataService interface so existing UI
pages work without modification.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable

import pandas as pd

from src.data import DataService

logger = logging.getLogger(__name__)


class ReplayState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass
class ReplayConfig:
    """Configuration for market replay."""

    start_date: str | None = None
    end_date: str | None = None
    speed: float = 1.0  # 1x = real-time, 2x = double speed, etc.
    symbol: str = "NEPSE"
    step_seconds: int = 3600  # seconds per step (1 hour for daily data)
    auto_loop: bool = False
    on_frame_callback: Callable[[pd.DataFrame, int], None] | None = None


@dataclass
class ReplaySnapshot:
    """Snapshot of current replay state."""

    state: ReplayState = ReplayState.STOPPED
    current_frame: int = 0
    total_frames: int = 0
    current_date: str = ""
    speed: float = 1.0
    progress_pct: float = 0.0
    symbol: str = ""
    elapsed_seconds: float = 0.0


class MarketReplayEngine:
    """Replay historical market data with play/pause/step controls.

    Usage::
        engine = MarketReplayEngine()
        engine.load(symbol="NEPSE", days=500)
        engine.play(speed=5.0)
        ...
        engine.pause()
        step = engine.step_forward()
        engine.stop()

    The engine emits frame callbacks that can be used to inject
    data into DataService's cache, making UI pages display
    historical data without modification.
    """

    def __init__(self) -> None:
        self._data: pd.DataFrame = pd.DataFrame()
        self._frames: list[pd.DataFrame] = []
        self._config = ReplayConfig()
        self._state = ReplayState.STOPPED
        self._current_frame = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._start_time: float = 0.0
        self._frame_callback: Callable[[pd.DataFrame, int], None] | None = None

    # ── Loading ──────────────────────────────────────────────────

    def load(self, symbol: str = "NEPSE", days: int = 500) -> bool:
        """Load historical data for replay from DataService."""
        try:
            svc = DataService()
            df = svc.get_nepse_index_history(days=days)
            if df is None or df.empty:
                # Try stock history
                hist = svc.get_history(symbol, days=days)
                df = hist.df if not hist.is_empty else pd.DataFrame()
            if df.empty:
                logger.warning("[Replay] No data available for %s", symbol)
                return False
            return self._prepare(df, symbol)
        except Exception as exc:
            logger.error("[Replay] Load failed: %s", exc)
            return False

    def load_dataframe(self, df: pd.DataFrame, symbol: str = "CUSTOM") -> bool:
        """Load a pre-loaded DataFrame for replay."""
        return self._prepare(df, symbol)

    def _prepare(self, df: pd.DataFrame, symbol: str) -> bool:
        """Prepare data for replay by splitting into time-ordered frames."""
        df = df.copy()
        # Ensure Date column
        if "Date" not in df.columns:
            df["Date"] = df.index if isinstance(df.index, pd.DatetimeIndex) else range(len(df))
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)

        self._data = df
        self._symbol = symbol
        self._frames = []
        self._current_frame = 0
        self._state = ReplayState.STOPPED

        # Create frames: one per row (each row is a time step)
        for i in range(len(df)):
            self._frames.append(df.iloc[: i + 1].copy())

        logger.info("[Replay] Loaded %d frames for %s", len(self._frames), symbol)
        return True

    # ── Controls ─────────────────────────────────────────────────

    def play(self, speed: float = 1.0) -> None:
        """Start playback at given speed multiplier."""
        with self._lock:
            if not self._frames:
                logger.warning("[Replay] No data loaded")
                return
            self._config.speed = max(0.1, speed)
            self._state = ReplayState.PLAYING
            self._start_time = time.time()

        self._stop_event.clear()
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="replay")
            self._thread.start()

        logger.info("[Replay] Playing at %.1fx speed", speed)

    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            if self._state == ReplayState.PLAYING:
                self._state = ReplayState.PAUSED
                logger.info("[Replay] Paused at frame %d/%d", self._current_frame, self.total_frames)

    def resume(self) -> None:
        """Resume from paused state."""
        with self._lock:
            if self._state == ReplayState.PAUSED:
                self._state = ReplayState.PLAYING
                self._start_time = time.time()
                logger.info("[Replay] Resumed")

    def stop(self) -> None:
        """Stop playback and reset to beginning."""
        with self._lock:
            self._state = ReplayState.STOPPED
            self._current_frame = 0
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("[Replay] Stopped")

    def step_forward(self, steps: int = 1) -> pd.DataFrame | None:
        """Advance one or more frames. Returns the current frame data."""
        with self._lock:
            if not self._frames:
                return None
            self._current_frame = min(self._current_frame + steps, len(self._frames) - 1)
            frame = self._frames[self._current_frame]
            self._emit_frame(frame)
            return frame

    def step_backward(self, steps: int = 1) -> pd.DataFrame | None:
        """Go back one or more frames. Returns the current frame data."""
        with self._lock:
            if not self._frames:
                return None
            self._current_frame = max(self._current_frame - steps, 0)
            frame = self._frames[self._current_frame]
            self._emit_frame(frame)
            return frame

    def set_speed(self, speed: float) -> None:
        """Change playback speed."""
        with self._lock:
            self._config.speed = max(0.1, min(100.0, speed))
            logger.info("[Replay] Speed set to %.1fx", self._config.speed)

    def go_to_frame(self, frame: int) -> pd.DataFrame | None:
        """Jump to a specific frame."""
        with self._lock:
            if not self._frames:
                return None
            self._current_frame = max(0, min(frame, len(self._frames) - 1))
            frame_data = self._frames[self._current_frame]
            self._emit_frame(frame_data)
            return frame_data

    def go_to_date(self, dt: str) -> pd.DataFrame | None:
        """Jump to the frame closest to a specific date."""
        try:
            target = pd.Timestamp(dt)
            with self._lock:
                df = self._data
                if "Date" in df.columns:
                    idx = (pd.to_datetime(df["Date"]) - target).abs().idxmin()
                    return self.go_to_frame(int(idx))
        except Exception:
            pass
        return None

    def fast_forward(self, factor: float = 5.0) -> None:
        """Temporarily increase speed for fast-forward."""
        current = self._config.speed
        self.set_speed(current * factor)
        # Schedule speed reset after 3 seconds
        def _reset() -> None:
            time.sleep(3)
            self.set_speed(current)
        threading.Thread(target=_reset, daemon=True).start()

    def rewind(self, factor: float = 5.0) -> None:
        """Rewind by going back many frames."""
        with self._lock:
            step = int(10 * factor)
            self._current_frame = max(0, self._current_frame - step)
            if self._frames:
                self._emit_frame(self._frames[self._current_frame])

    # ── Frame callback ───────────────────────────────────────────

    def set_frame_callback(self, callback: Callable[[pd.DataFrame, int], None]) -> None:
        """Set a callback that fires on every frame change.

        The callback receives (frame_dataframe, frame_index).
        """
        self._frame_callback = callback

    def _emit_frame(self, frame: pd.DataFrame) -> None:
        """Emit the current frame to the callback."""
        if self._frame_callback:
            try:
                self._frame_callback(frame, self._current_frame)
            except Exception as exc:
                logger.warning("[Replay] Callback error: %s", exc)

    # ── Playback loop ────────────────────────────────────────────

    def _run(self) -> None:
        """Main playback loop."""
        base_interval = self._config.step_seconds
        while not self._stop_event.is_set():
            # Get frame info without holding lock during callback
            with self._lock:
                if self._state == ReplayState.STOPPED:
                    break
                if self._state == ReplayState.PAUSED:
                    delay = 0.1
                    callback_frame = None
                elif self._current_frame >= len(self._frames) - 1:
                    if self._config.auto_loop:
                        self._current_frame = 0
                        delay = base_interval / self._config.speed
                        callback_frame = self._frames[0]
                    else:
                        self._state = ReplayState.FINISHED
                        break
                else:
                    callback_frame = self._frames[self._current_frame]
                    delay = base_interval / self._config.speed
                    self._current_frame += 1

            # Emit frame OUTSIDE the lock to avoid contention
            if callback_frame is not None:
                self._emit_frame(callback_frame)

            # Wait for next frame or stop
            self._stop_event.wait(min(delay if isinstance(delay, (int, float)) else 0.1, 1.0))
        self._state = ReplayState.FINISHED if not self._stop_event.is_set() else ReplayState.STOPPED

    # ── State queries ────────────────────────────────────────────

    @property
    def state(self) -> ReplayState:
        with self._lock:
            return self._state

    @property
    def current_frame(self) -> int:
        with self._lock:
            return self._current_frame

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def current_data(self) -> pd.DataFrame:
        with self._lock:
            if self._frames and 0 <= self._current_frame < len(self._frames):
                return self._frames[self._current_frame]
            return pd.DataFrame()

    def get_snapshot(self) -> ReplaySnapshot:
        """Return a snapshot of the current replay state."""
        with self._lock:
            current_date = ""
            frame = self.current_data
            if not frame.empty and "Date" in frame.columns:
                current_date = str(frame["Date"].iloc[-1])
            return ReplaySnapshot(
                state=self._state,
                current_frame=self._current_frame,
                total_frames=self.total_frames,
                current_date=current_date,
                speed=self._config.speed,
                progress_pct=(self._current_frame / max(self.total_frames, 1)) * 100,
                symbol=self._symbol,
                elapsed_seconds=time.time() - self._start_time if self._start_time > 0 else 0.0,
            )

    # ── DataService injection ────────────────────────────────────

    def inject_into_cache(self) -> None:
        """Inject the current frame into DataService's cache.

        This allows existing UI pages to display historical data
        without any code changes — they read from cache which
        now contains replayed data.
        """
        frame = self.current_data
        if frame.empty:
            return
        try:
            svc = DataService()
            svc._cache.set("replay_active", True, ttl=3600)
            svc._cache.set("replay_data", frame, ttl=3600)
            svc._cache.set("replay_frame", self._current_frame, ttl=3600)
            svc._cache.set("replay_total", self.total_frames, ttl=3600)

            # Also inject as nepse_index_history so dashboard regime works
            svc._cache.set("nepse_index:500", frame, ttl=3600)

            # Inject as MarketSummary object (not dict — pages expect .status, .advances, etc.)
            if not frame.empty and "Close" in frame.columns:
                last_close = float(frame["Close"].iloc[-1])
                first_close = float(frame["Close"].iloc[0]) if len(frame) > 1 else last_close
                from src.data.models import MarketSummary
                summary = MarketSummary(
                    index=last_close,
                    change=last_close - first_close,
                    change_pct=((last_close - first_close) / first_close) * 100 if first_close > 0 else 0,
                    status="Replay",
                    advances=0, declines=0, volume=0, turnover=0.0,
                )
                svc._cache.set("market_summary", summary, ttl=3600)

            logger.info("[Replay] Injected frame %d/%d into cache", self._current_frame, self.total_frames)
        except Exception as exc:
            logger.warning("[Replay] Cache injection error: %s", exc)

    def clear_cache_injection(self) -> None:
        """Remove replay data from cache, restoring live mode."""
        try:
            svc = DataService()
            svc._cache.delete("replay_active")
            svc._cache.delete("replay_data")
            svc._cache.delete("replay_frame")
            svc._cache.delete("replay_total")
            svc._cache.delete("nepse_index:500")
            svc._cache.delete("market_summary")
            logger.info("[Replay] Cache injection cleared, live mode restored")
        except Exception as exc:
            logger.warning("[Replay] Cache clear error: %s", exc)
