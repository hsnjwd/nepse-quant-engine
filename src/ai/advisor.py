"""AI Strategy Advisor — natural-language explanations for signals.

The advisor is fully rule-based: it inspects indicator values, market
regime, and volume behaviour to explain *why* a signal was generated
in a human-readable form.  The rule-based implementation is designed
so it can later be replaced with an LLM-backed generator without
changing the public interface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.logging.logger import logger


@dataclass
class SignalExplanation:
    """Structured explanation of a trading signal.

    Attributes:
        symbol: Stock symbol.
        signal: BUY / HOLD / SELL / STRONG_BUY / STRONG_SELL.
        confidence: Confidence percentage (0–100).
        reasons: Ordered list of human-readable reason bullets.
        risks: Optional list of risk considerations.
        summary: One-line plain-language summary.
        recommendation: Actionable recommendation text.
    """

    symbol: str
    signal: str = "HOLD"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    summary: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "symbol": self.symbol,
            "signal": self.signal,
            "confidence": round(self.confidence, 2),
            "reasons": self.reasons,
            "risks": self.risks,
            "summary": self.summary,
            "recommendation": self.recommendation,
        }


class SignalAdvisor:
    """Generate rule-based explanations for trading signals.

    Usage::

        advisor = SignalAdvisor()
        explanation = advisor.explain(
            symbol="NABIL",
            analysis={"rsi": 42.0, "macd": 1.5, "signal": "BUY"},
            regime="BULL",
        )
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explain(
        self,
        symbol: str,
        analysis: dict[str, Any] | None = None,
        regime: str | None = None,
        signal: str | None = None,
        confidence: float | None = None,
    ) -> SignalExplanation:
        """Build an explanation for a signal.

        Args:
            symbol: Stock symbol.
            analysis: Optional analysis dict from the analyzer engine
                (keys such as ``rsi``, ``macd``, ``atr``, ``signal``,
                ``score``, ``confidence``).
            regime: Optional market regime label.
            signal: Explicit signal override.
            confidence: Explicit confidence override.

        Returns:
            A :class:`SignalExplanation`.
        """
        analysis = analysis or {}
        signal = (signal or analysis.get("signal") or "HOLD").upper()
        raw_confidence = (
            confidence
            if confidence is not None
            else analysis.get("confidence")
        )
        try:
            confidence = float(raw_confidence) if raw_confidence is not None else 0.0
        except (TypeError, ValueError):
            confidence = 0.0

        reasons = self._build_reasons(analysis, regime)
        risks = self._build_risks(analysis)
        summary = self._build_summary(symbol, signal, confidence, reasons, regime)
        recommendation = self._build_recommendation(signal, summary, analysis)

        explanation = SignalExplanation(
            symbol=symbol.upper(),
            signal=signal,
            confidence=confidence,
            reasons=reasons,
            risks=risks,
            summary=summary,
            recommendation=recommendation,
        )
        logger.debug("Advisor explanation generated for %s: %s", symbol, signal)
        return explanation

    def explain_many(
        self,
        results: list[dict[str, Any]],
        regime: str | None = None,
    ) -> list[SignalExplanation]:
        """Explain several signals at once.

        Args:
            results: List of dicts, each containing at least ``symbol``
                and optionally ``analysis``/``signal``/``confidence``.
            regime: Optional shared market regime.

        Returns:
            One :class:`SignalExplanation` per input.
        """
        return [
            self.explain(
                symbol=item.get("symbol", "UNKNOWN"),
                analysis=item.get("analysis"),
                regime=regime,
                signal=item.get("signal"),
                confidence=item.get("confidence"),
            )
            for item in results
        ]

    # ------------------------------------------------------------------
    # Reason builders
    # ------------------------------------------------------------------

    def _build_reasons(
        self,
        analysis: dict[str, Any],
        regime: str | None,
    ) -> list[str]:
        """Generate the ordered reason bullets."""
        reasons: list[str] = []

        rsi = analysis.get("rsi")
        if rsi is not None:
            if rsi < 30:
                reasons.append(f"RSI at {rsi:.1f} — oversold, recovery potential")
            elif rsi < 45:
                reasons.append(f"RSI at {rsi:.1f} — recovering from oversold zone")
            elif rsi > 70:
                reasons.append(f"RSI at {rsi:.1f} — overbought, pullback risk")
            else:
                reasons.append(f"RSI at {rsi:.1f} — neutral momentum zone")

        macd = analysis.get("macd")
        macd_signal = analysis.get("macd_signal")
        if macd is not None and macd_signal is not None:
            if macd > macd_signal:
                reasons.append("MACD above its signal line — bullish crossover")
            else:
                reasons.append("MACD below its signal line — bearish crossover")

        score = analysis.get("score")
        if score is not None:
            reasons.append(f"Composite score of {score:.1f} across indicators")

        if regime:
            reasons.append(f"Market regime is {regime.upper()}")

        pattern = analysis.get("pattern")
        if pattern and pattern not in ("None", "UNKNOWN", ""):
            reasons.append(f"Candlestick pattern detected: {pattern}")

        price = analysis.get("price")
        ema = analysis.get("ema20", analysis.get("EMA_20"))
        if price is not None and ema is not None:
            if price > ema:
                reasons.append(f"Price {price:.2f} trading above EMA20 ({ema:.2f})")
            else:
                reasons.append(f"Price {price:.2f} trading below EMA20 ({ema:.2f})")

        volume_signal = analysis.get("volume_signal")
        if volume_signal == "VOLUME_SPIKE":
            reasons.append("Volume spike confirms strong participation")
        elif volume_signal == "HIGH_VOLUME":
            reasons.append("Above-average volume supports the move")
        elif volume_signal == "LOW_VOLUME":
            reasons.append("Thin volume — signal may lack conviction")

        obv = analysis.get("obv")
        if obv is not None:
            reasons.append(
                "OBV confirms accumulation" if obv > 0 else "OBV shows distribution"
            )

        if not reasons:
            reasons.append("No strong technical confluence detected")

        return reasons

    @staticmethod
    def _build_risks(analysis: dict[str, Any]) -> list[str]:
        """Generate risk considerations."""
        risks: list[str] = []
        atr = analysis.get("atr")
        price = analysis.get("price")
        if atr is not None and price:
            atr_pct = atr / price * 100
            if atr_pct > 3:
                risks.append(f"High volatility (ATR {atr_pct:.1f}% of price)")
        if analysis.get("volume_signal") == "LOW_VOLUME":
            risks.append("Low volume may produce false signals")
        return risks

    @staticmethod
    def _build_summary(
        symbol: str,
        signal: str,
        confidence: float,
        reasons: list[str],
        regime: str | None,
    ) -> str:
        """Compose the one-line summary."""
        parts = [f"{symbol.upper()} shows a {signal} signal"]
        if confidence:
            parts.append(f"with {confidence:.0f}% confidence")
        if reasons:
            parts.append(f"({len(reasons)} supporting factors)")
        if regime:
            parts.append(f"in a {regime.upper()} market")
        return " ".join(parts)

    @staticmethod
    def _build_recommendation(
        signal: str,
        summary: str,
        analysis: dict[str, Any],
    ) -> str:
        """Compose actionable recommendation text."""
        price = analysis.get("price")
        support = analysis.get("support")
        resistance = analysis.get("resistance")

        base = f"{summary}."
        if signal in ("BUY", "STRONG_BUY"):
            advice = "Consider accumulating on dips"
            if price and support:
                advice += f" near support at {support:.2f}"
            if price and resistance:
                advice += f", with first target near {resistance:.2f}"
            return f"{base} {advice}."
        if signal in ("SELL", "STRONG_SELL"):
            advice = "Consider reducing exposure"
            if price and resistance:
                advice += f" near resistance at {resistance:.2f}"
            return f"{base} {advice}."
        return f"{base} Monitor price action for a clearer setup."
