"""Corporate actions handling for the institutional backtesting engine.

Part 9.5 — stock splits, reverse splits, bonus shares, cash dividends,
rights issues, delisting, and symbol changes.  Backtests automatically
adjust historical data (prices and volumes) so returns remain
economically consistent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_ACTION_KINDS = (
    "split",
    "reverse_split",
    "bonus",
    "dividend",
    "rights",
    "delisting",
    "symbol_change",
)


@dataclass
class CorporateAction:
    """A single corporate action.

    Attributes:
        kind: One of the supported action kinds.
        symbol: Ticker symbol affected.
        date: Effective date (ISO string or datetime).
        ratio: Ratio for splits/bonus (e.g. 2.0 = 2-for-1 split,
            1.5 = 50% bonus).
        dividend: Cash dividend per share (for dividends).
        to_symbol: Replacement symbol (for symbol_change).
        reason: Optional human-readable reason.
    """

    kind: str
    symbol: str
    date: Any = None
    ratio: float = 1.0
    dividend: float = 0.0
    to_symbol: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        """Validate the action kind."""
        if self.kind not in _ACTION_KINDS:
            raise ValueError(
                f"Unsupported corporate action kind '{self.kind}'. "
                f"Supported: {list(_ACTION_KINDS)}"
            )

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "CorporateAction":
        """Build a CorporateAction from a dictionary payload."""
        known = {f: payload.get(f) for f in ("kind", "symbol", "date", "ratio", "dividend", "to_symbol", "reason")}
        return CorporateAction(**known)


def adjust_price(df: pd.DataFrame, ratio: float) -> pd.DataFrame:
    """Divide OHLC by *ratio* (for splits and bonus issues).

    Args:
        df: OHLCV DataFrame.
        ratio: Adjustment ratio (> 1 for splits, < 1 for reverse).

    Returns:
        The adjusted DataFrame (in place of a copy).
    """
    frame = df.copy()
    if ratio <= 0:
        return frame
    for col in ("Open", "High", "Low", "Close"):
        if col in frame.columns:
            frame[col] = frame[col].astype(float) / ratio
    if "Volume" in frame.columns:
        frame["Volume"] = frame["Volume"].astype(float) * ratio
    return frame


def apply_dividend(df: pd.DataFrame, dividend: float, date: Any) -> pd.DataFrame:
    """Subtract a cash dividend from prices on and before *date*.

    Args:
        df: OHLCV DataFrame.
        dividend: Cash dividend per share.
        date: Effective date.

    Returns:
        The adjusted DataFrame.
    """
    frame = df.copy()
    if dividend <= 0:
        return frame
    date_col = "Date" if "Date" in frame.columns else frame.index.name
    if date_col is None:
        return frame
    dates = pd.to_datetime(frame[date_col] if date_col in frame.columns else frame.index)
    effective = pd.Timestamp(date)
    mask = dates <= effective
    for col in ("Open", "High", "Low", "Close"):
        if col in frame.columns:
            frame.loc[mask, col] = frame.loc[mask, col].astype(float) - dividend
    return frame


class CorporateActionEngine:
    """Applies a list of corporate actions to a symbol DataFrame set.

    Splits/bonus adjust all history proportionally; dividends adjust
    prices before the ex-date; delisting drops later bars; symbol
    changes rename the key.
    """

    def __init__(self, actions: list[dict[str, Any]] | list[CorporateAction] | None = None) -> None:
        """Initialise with an optional action list.

        Args:
            actions: List of action dicts or CorporateAction instances.
        """
        self._actions: list[CorporateAction] = []
        for item in actions or []:
            if isinstance(item, CorporateAction):
                self._actions.append(item)
            else:
                self._actions.append(CorporateAction.from_dict(item))

    def add(self, action: CorporateAction | dict[str, Any]) -> None:
        """Append an action."""
        if isinstance(action, CorporateAction):
            self._actions.append(action)
        else:
            self._actions.append(CorporateAction.from_dict(action))

    def actions_for(self, symbol: str) -> list[CorporateAction]:
        """Return actions affecting *symbol* (sorted by date)."""
        def _key(a: CorporateAction) -> str:
            return str(a.date) if a.date else ""
        return sorted(
            [a for a in self._actions if a.symbol == symbol],
            key=_key,
        )

    def apply_all(self, data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        """Apply all actions to the supplied DataFrames.

        Args:
            data: Mapping of symbol -> OHLCV DataFrame.

        Returns:
            A new mapping with adjusted frames.  Symbols that delisted
            are dropped; symbol changes rename the key.
        """
        result: dict[str, pd.DataFrame] = {}
        for symbol, df in data.items():
            adjusted = self.apply_to_symbol(symbol, df)
            if adjusted is None:
                logger.info("[CorporateAction] %s delisted; dropped from backtest", symbol)
                continue
            result[symbol] = adjusted

        # Apply symbol changes after the loop (renames may reference
        # symbols that were processed already).
        for action in self._actions:
            if action.kind == "symbol_change" and action.to_symbol:
                if action.symbol in result and action.to_symbol not in result:
                    result[action.to_symbol] = result.pop(action.symbol)
                    logger.info("[CorporateAction] %s -> %s", action.symbol, action.to_symbol)

        return result

    def apply_to_symbol(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame | None:
        """Apply all actions for *symbol* to its DataFrame.

        Args:
            symbol: Ticker symbol.
            df: OHLCV DataFrame.

        Returns:
            Adjusted DataFrame, or ``None`` when the symbol delisted.
        """
        frame = df.copy()
        for action in self.actions_for(symbol):
            if action.kind in ("split", "reverse_split"):
                ratio = action.ratio if action.kind == "split" else (1.0 / action.ratio if action.ratio > 0 else 1.0)
                frame = adjust_price(frame, ratio)
                logger.debug("[CorporateAction] %s %s ratio=%.2f", symbol, action.kind, ratio)
            elif action.kind == "bonus":
                frame = adjust_price(frame, 1.0 + max(action.ratio, 0.0))
                logger.debug("[CorporateAction] %s bonus ratio=%.2f", symbol, action.ratio)
            elif action.kind == "dividend":
                frame = apply_dividend(frame, action.dividend, action.date)
                logger.debug("[CorporateAction] %s dividend=%.2f", symbol, action.dividend)
            elif action.kind == "delisting":
                if action.date is not None:
                    frame = frame[frame["Date"] <= pd.Timestamp(action.date)] if "Date" in frame.columns else frame
                else:
                    return None
            # symbol_change handled in apply_all.

        if frame.empty:
            return None
        return frame

    def __len__(self) -> int:
        """Return the number of registered actions."""
        return len(self._actions)
