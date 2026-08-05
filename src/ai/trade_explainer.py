"""Trade explainer for the AI Strategy Advisor.

Explains entry and exit decisions in natural language based on
indicator confluence, risk/reward, and market regime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nepse.ai.trade_explainer")


@dataclass
class TradeExplanation:
    """Explanation of a trade decision.

    Attributes:
        side: ``"BUY"`` or ``"SELL"``.
        symbol: Stock symbol.
        entry_reasons: Reasons supporting the entry.
        exit_reasons: Reasons supporting the exit.
        risk_notes: Risk-related commentary.
        entry_price: Optional reference entry price.
        stop_loss: Optional stop-loss level.
        targets: Optional profit targets.
        strategy: Optional strategy name.
        confidence: Optional confidence percentage.
    """

    side: str = "BUY"
    symbol: str = ""
    entry_reasons: list[str] = field(default_factory=list)
    exit_reasons: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    entry_price: float | None = None
    stop_loss: float | None = None
    targets: list[float] = field(default_factory=list)
    strategy: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "side": self.side,
            "symbol": self.symbol,
            "entry_reasons": self.entry_reasons,
            "exit_reasons": self.exit_reasons,
            "risk_notes": self.risk_notes,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "strategy": self.strategy,
            "confidence": self.confidence,
        }


class TradeExplainer:
    """Generate natural-language trade decision explanations.

    Usage::

        explainer = TradeExplainer()
        explanation = explainer.explain_entry(
            symbol="NABIL",
            analysis={"rsi": 32.0, "price": 500.0},
            side="BUY",
        )
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explain_entry(
        self,
        symbol: str,
        analysis: dict[str, Any] | None = None,
        side: str = "BUY",
        strategy: str | None = None,
    ) -> TradeExplanation:
        """Explain an entry decision.

        Args:
            symbol: Stock symbol.
            analysis: Optional analysis dict with indicator values.
            side: BUY or SELL.
            strategy: Optional strategy name.

        Returns:
            A :class:`TradeExplanation`.
        """
        analysis = analysis or {}
        side = "BUY" if side.upper() in ("BUY", "LONG") else "SELL"

        entry_reasons = self._entry_reasons(analysis, side)
        risk_notes = self._risk_notes(analysis)

        return TradeExplanation(
            side=side,
            symbol=symbol.upper(),
            entry_reasons=entry_reasons,
            risk_notes=risk_notes,
            entry_price=analysis.get("price"),
            stop_loss=analysis.get("stop_loss"),
            targets=[float(t) for t in analysis.get("targets", []) if t is not None],
            strategy=strategy or analysis.get("strategy_name", ""),
            confidence=analysis.get("confidence"),
        )

    def explain_exit(
        self,
        symbol: str,
        analysis: dict[str, Any] | None = None,
        side: str = "SELL",
        realized_pnl: float | None = None,
    ) -> TradeExplanation:
        """Explain an exit decision.

        Args:
            symbol: Stock symbol.
            analysis: Optional analysis dict.
            side: BUY or SELL.
            realized_pnl: Optional realised profit/loss.

        Returns:
            A :class:`TradeExplanation`.
        """
        analysis = analysis or {}
        side = "SELL" if side.upper() in ("SELL", "SHORT") else "BUY"
        exit_reasons = self._exit_reasons(analysis, realized_pnl)

        return TradeExplanation(
            side=side,
            symbol=symbol.upper(),
            exit_reasons=exit_reasons,
            entry_price=analysis.get("price"),
            stop_loss=analysis.get("stop_loss"),
            confidence=analysis.get("confidence"),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _entry_reasons(
        self,
        analysis: dict[str, Any],
        side: str,
    ) -> list[str]:
        """Build entry reasons for the requested side."""
        reasons: list[str] = []
        rsi = analysis.get("rsi")
        if rsi is not None:
            if side == "BUY" and rsi < 40:
                reasons.append(f"RSI at {rsi:.1f} favours entry from a low zone")
            elif side == "SELL" and rsi > 60:
                reasons.append(f"RSI at {rsi:.1f} suggests extended upside")

        macd = analysis.get("macd")
        macd_signal = analysis.get("macd_signal")
        if macd is not None and macd_signal is not None:
            cross = "bullish" if macd > macd_signal else "bearish"
            if (side == "BUY" and cross == "bullish") or (
                side == "SELL" and cross == "bearish"
            ):
                reasons.append(f"MACD shows a {cross} configuration")

        price = analysis.get("price")
        support = analysis.get("support")
        resistance = analysis.get("resistance")
        if side == "BUY" and price and support:
            reasons.append(f"Price near support zone at {support:.2f}")
        if side == "SELL" and price and resistance:
            reasons.append(f"Price near resistance zone at {resistance:.2f}")

        if not reasons:
            reasons.append(
                "Entry based on overall signal confluence and trend alignment"
            )
        return reasons

    @staticmethod
    def _exit_reasons(
        analysis: dict[str, Any],
        realized_pnl: float | None,
    ) -> list[str]:
        """Build exit reasons."""
        reasons: list[str] = []
        if realized_pnl is not None:
            label = "profit" if realized_pnl >= 0 else "loss"
            reasons.append(f"Trade closed with a {label} of {abs(realized_pnl):,.2f}")

        target_hit = analysis.get("target_hit")
        if target_hit:
            reasons.append("Primary profit target was reached")

        stop_hit = analysis.get("stop_hit")
        if stop_hit:
            reasons.append("Stop-loss level was triggered")

        if not reasons:
            reasons.append("Exit signal triggered by strategy rules")
        return reasons

    @staticmethod
    def _risk_notes(analysis: dict[str, Any]) -> list[str]:
        """Build risk commentary."""
        notes: list[str] = []
        atr = analysis.get("atr")
        price = analysis.get("price")
        if atr is not None and price:
            pct = atr / price * 100
            if pct > 3:
                notes.append(f"Wide stops advised (ATR {pct:.1f}% of price)")
        stop_loss = analysis.get("stop_loss")
        if stop_loss and price:
            distance = abs(price - stop_loss) / price * 100
            notes.append(f"Stop-loss is {distance:.1f}% from entry")
        return notes
