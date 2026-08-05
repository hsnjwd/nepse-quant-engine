"""Replay session — thin stateful wrapper around MarketReplayEngine.

Provides a session-oriented API (session id, step, speed, state) used
by the replay API router and the replay UI.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import numpy as np
import pandas as pd

from src.replay.engine import MarketReplayEngine

logger = logging.getLogger("nepse.replay.session")


class ReplaySession:
    """A single replay session over one symbol's history.

    Wraps :class:`MarketReplayEngine` with session identity and a
    simple synchronous step/state interface.

    Usage::

        session = ReplaySession(df)
        session.step(5)
        state = session.state()
    """

    def __init__(self, df: pd.DataFrame, symbol: str = "CUSTOM") -> None:
        """Initialise the session.

        Args:
            df: OHLCV DataFrame to replay.
            symbol: Optional symbol label.
        """
        self.session_id = uuid.uuid4().hex[:12]
        self._engine = MarketReplayEngine()
        loaded = self._engine.load_dataframe(df, symbol=symbol)
        if not loaded:
            raise ValueError("Replay data could not be loaded (empty frame).")
        logger.info(
            "Replay session %s started with %d bars.",
            self.session_id,
            self.total_bars,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def total_bars(self) -> int:
        """Return the total number of frames."""
        return self._engine.total_frames

    @property
    def current_frame(self) -> int:
        """Return the current frame index."""
        return self._engine.current_frame

    @property
    def speed(self) -> float:
        """Return the playback speed multiplier."""
        return self._engine.get_snapshot().speed

    @speed.setter
    def speed(self, value: float) -> None:
        """Set the playback speed multiplier."""
        self._engine.set_speed(value)

    @property
    def engine(self) -> MarketReplayEngine:
        """Return the underlying replay engine."""
        return self._engine

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def step(self, steps: int = 1) -> dict[str, Any]:
        """Advance the replay by *steps* bars.

        Args:
            steps: Number of bars to advance (may be negative to rewind).

        Returns:
            The updated state dict.
        """
        if steps >= 0:
            self._engine.step_forward(steps)
        else:
            self._engine.step_backward(abs(steps))
        return self.state()

    def reset(self) -> dict[str, Any]:
        """Jump back to the first frame.

        Returns:
            The updated state dict.
        """
        self._engine.go_to_frame(0)
        return self.state()

    def state(self) -> dict[str, Any]:
        """Return the current session state.

        Returns:
            Dict with session metadata, frame info, and the current
            OHLCV bar.
        """
        snapshot = self._engine.get_snapshot()
        frame = self._engine.current_data

        bar: dict[str, Any] = {}
        if not frame.empty:
            latest = frame.iloc[-1]
            for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
                if col in frame.columns:
                    value = latest[col]
                    if hasattr(value, "isoformat"):
                        # Timestamps / datetimes -> ISO strings.
                        bar[col] = value.isoformat()
                    elif isinstance(value, np.generic):
                        # numpy scalars are not JSON-serialisable by
                        # FastAPI/pydantic without the numpy encoder.
                        bar[col] = value.item()
                    else:
                        bar[col] = value

        return {
            "session_id": self.session_id,
            "state": snapshot.state.value,
            "current_frame": snapshot.current_frame,
            "total_frames": snapshot.total_frames,
            "progress_pct": round(snapshot.progress_pct, 2),
            "speed": snapshot.speed,
            "current_date": snapshot.current_date,
            "bar": bar,
        }
