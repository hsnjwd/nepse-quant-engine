"""Replay ↔ Backtest integration bridge (Part 9.13).

Connects the existing :class:`MarketReplayEngine` to the new
:class:`BacktestEngine` so users can replay bars, pause, inspect
indicators / orders / AI decisions, and continue — with the backtest
executing the same orders synchronously.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.models import Bar, Order
from src.replay.engine import MarketReplayEngine

logger = logging.getLogger(__name__)


@dataclass
class ReplayBacktestState:
    """State shared between replay playback and the backtest engine.

    Attributes:
        symbol: The symbol being replayed.
        bar_index: Current bar index.
        timestamp: Current timestamp.
        last_bar: The most recent bar processed.
        orders_placed: Orders submitted by the strategy so far.
        decisions: List of strategy decision dicts.
        backtest_complete: Whether a full backtest has been run.
    """

    symbol: str = ""
    bar_index: int = 0
    timestamp: Any = None
    last_bar: dict[str, Any] = field(default_factory=dict)
    orders_placed: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    backtest_complete: bool = False


class ReplayBacktestBridge:
    """Synchronises replay frames with backtest order execution.

    Usage::

        bridge = ReplayBacktestBridge(strategy=strategy_fn)
        bridge.load(df, symbol="NABIL")
        bridge.replay.pause()
        frame = bridge.step()          # advance one frame
        decision = bridge.current_decision()
        bridge.replay.play(speed=5.0)
        result = bridge.run_full_backtest()
    """

    def __init__(
        self,
        strategy: Callable[[int, dict[str, Bar], Any], list[Order]] | None = None,
        engine: BacktestEngine | None = None,
    ) -> None:
        """Initialise the bridge.

        Args:
            strategy: Strategy callable shared by replay analysis and
                the backtest engine.
            engine: Optional pre-configured :class:`BacktestEngine`.
        """
        self.strategy = strategy
        self.replay = MarketReplayEngine()
        self.engine = engine
        self.state = ReplayBacktestState()
        self._result: BacktestResult | None = None

    # ── Loading ──────────────────────────────────────────────────

    def load(
        self,
        df: pd.DataFrame,
        symbol: str = "CUSTOM",
        strategy: Callable[[int, dict[str, Bar], Any], list[Order]] | None = None,
    ) -> bool:
        """Load a DataFrame into the replay engine.

        Args:
            df: OHLCV DataFrame.
            symbol: Symbol name.
            strategy: Optional strategy to override the constructor one.

        Returns:
            ``True`` when loading succeeded.
        """
        if strategy is not None:
            self.strategy = strategy
        ok = self.replay.load_dataframe(df, symbol)
        if ok:
            self.state.symbol = symbol
            self.state.bar_index = 0
            logger.info("[ReplayBacktest] Loaded %s (%d frames)", symbol, self.replay.total_frames)
        return ok

    # ── Step-through with decision inspection ────────────────────

    def _frame_to_bars(self, frame: pd.DataFrame, symbol: str | None = None) -> dict[str, Bar]:
        """Convert the last row of a replay frame to an engine Bar dict."""
        if frame is None or frame.empty:
            return {}
        row = frame.iloc[-1]
        sym = symbol or self.state.symbol
        return {
            sym: Bar(
                symbol=sym,
                timestamp=row.get("Date", 0),
                open=float(row.get("Open", 0.0)),
                high=float(row.get("High", 0.0)),
                low=float(row.get("Low", 0.0)),
                close=float(row.get("Close", 0.0)),
                volume=float(row.get("Volume", 0.0) or 0.0),
                index=len(frame) - 1,
            )
        }

    def current_decision(self, frame: pd.DataFrame | None = None) -> dict[str, Any]:
        """Run the strategy on the current frame and return its decision.

        Args:
            frame: Optional frame to evaluate (defaults to current).

        Returns:
            A decision dict (or an empty dict when no strategy).
        """
        if self.strategy is None:
            return {}
        frame = frame if frame is not None else self.replay.current_data
        if frame is None or frame.empty:
            return {}
        bars = self._frame_to_bars(frame)
        try:
            decision = self.strategy(self.state.bar_index, bars, {"frame": frame})
            self.state.decisions.append({
                "bar_index": self.state.bar_index,
                "timestamp": str(self.replay.get_snapshot().current_date),
                "orders": [o.to_dict() for o in decision] if decision else [],
            })
            return decision
        except Exception as exc:
            logger.warning("[ReplayBacktest] Strategy error: %s", exc)
            return {"error": str(exc)}

    def step(self, steps: int = 1) -> pd.DataFrame | None:
        """Advance the replay by *steps* frames.

        Args:
            steps: Number of frames to advance.

        Returns:
            The new current frame (or None when exhausted).
        """
        frame = self.replay.step_forward(steps)
        self.state.bar_index = self.replay.current_frame
        if frame is not None and not frame.empty:
            self.state.timestamp = frame["Date"].iloc[-1]
            self.state.last_bar = frame.iloc[-1].to_dict()
        return frame

    def inspect_orders(self) -> list[dict[str, Any]]:
        """Return orders placed so far (from the backtest if run)."""
        if self._result is not None:
            return [o.to_dict() for o in self._result.orders]
        return list(self.state.orders_placed)

    # ── Full backtest on the loaded data ─────────────────────────

    def run_full_backtest(self, df: pd.DataFrame | None = None) -> BacktestResult:
        """Run a complete backtest on the replay data.

        Args:
            df: Optional DataFrame override (defaults to replay data).

        Returns:
            A :class:`BacktestResult`.
        """
        frame = df if df is not None else self.replay.current_data
        if frame is None or frame.empty:
            logger.warning("[ReplayBacktest] No data for full backtest")
            return BacktestResult()
        engine = self.engine or BacktestEngine(strategy=self.strategy)
        self._result = engine.run({self.state.symbol or "_replay": frame})
        self.state.backtest_complete = True
        return self._result

    @property
    def result(self) -> BacktestResult | None:
        """Return the last full-backtest result (or None)."""
        return self._result

    def reset(self) -> None:
        """Reset replay and bridge state."""
        self.replay.stop()
        self.state = ReplayBacktestState()
        self._result = None
