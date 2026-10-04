"""LEGACY decision helpers - DEPRECATED.

Use ``src.decision.confidence.calculate_confidence`` for confidence scores
and ``src.recommendations.trade_plan.create_trade_plan`` for trade plans
instead.

This module is kept ONLY for backward compatibility with legacy callers
and will be removed in a future release. Do not import it in new code.

TODO(v1.1): remove ``src/decision/engine.py`` and migrate remaining
consumers to ``src.decision.confidence`` / ``src.recommendations.trade_plan``.
"""

from __future__ import annotations

import warnings

_DEPRECATION_MSG = (
    "src.decision.engine is deprecated - use "
    "src.decision.confidence.calculate_confidence(score, breakdown) or "
    "src.recommendations.trade_plan.create_trade_plan(result)."
)


def calculate_confidence(score):
    """
    Convert technical score into confidence %
    """

    warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
    confidence = abs(score) * 10

    if confidence > 95:
        confidence = 95

    return confidence



def generate_trade_plan(row, score, signal):

    warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)

    price = row["Close"]


    # Default response
    plan = {
        "price": price,
        "score": score,
        "signal": signal,
        "confidence": calculate_confidence(score)
    }


    if signal == "BUY":

        plan["entry_zone"] = (
            round(price * 0.98, 2),
            round(price * 1.02, 2)
        )

        plan["target_1"] = round(price * 1.05, 2)
        plan["target_2"] = round(price * 1.10, 2)
        plan["target_3"] = round(price * 1.15, 2)

        plan["stop_loss"] = round(price * 0.95, 2)


    elif signal == "SELL":

        plan["entry_zone"] = None

        plan["target_1"] = round(price * 0.95, 2)
        plan["target_2"] = round(price * 0.90, 2)
        plan["target_3"] = round(price * 0.85, 2)

        plan["stop_loss"] = round(price * 1.05, 2)


    else:

        plan["entry_zone"] = None
        plan["target_1"] = None
        plan["target_2"] = None
        plan["target_3"] = None
        plan["stop_loss"] = None


    return plan


__all__ = ["calculate_confidence", "generate_trade_plan"]