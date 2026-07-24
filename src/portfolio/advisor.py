def build_advice(holding):

    analysis = holding["analysis"]

    advice = []

    signal = analysis["signal"]

    confidence = analysis["confidence"]

    trend = analysis["trend"]

    rr = analysis["best_rr"]

    pnl = holding["pnl_pct"]

    score = analysis["score"]

    # ------------------------
    # Signal
    # ------------------------

    if signal == "BUY":
        advice.append("BUY signal is active.")

    elif signal == "SELL":
        advice.append("SELL signal is active.")

    else:
        advice.append("No confirmed trading signal.")

    # ------------------------
    # Confidence
    # ------------------------

    if confidence >= 90:
        advice.append("Very high confidence setup.")

    elif confidence >= 75:
        advice.append("Good confidence.")

    else:
        advice.append("Confidence is moderate.")

    # ------------------------
    # Trend
    # ------------------------

    if trend == "UPTREND":
        advice.append("Primary trend is bullish.")

    elif trend == "SIDEWAYS":
        advice.append("Market is consolidating.")

    else:
        advice.append("Primary trend is bearish.")

    # ------------------------
    # Risk Reward
    # ------------------------

    if rr >= 3:
        advice.append("Excellent reward relative to risk.")

    elif rr >= 2:
        advice.append("Healthy reward/risk profile.")

    else:
        advice.append("Limited upside versus risk.")

    # ------------------------
    # Profit
    # ------------------------

    if pnl >= 25:
        advice.append("Consider booking partial profits.")

    elif pnl < -10:
        advice.append("Review stop-loss discipline.")

    # ------------------------
    # Recommendation
    # ------------------------

    if signal == "BUY" and confidence >= 90:

        recommendation = "STRONG ADD"

    elif signal == "BUY":

        recommendation = "ADD"

    elif signal == "SELL":

        recommendation = "EXIT"

    elif trend == "DOWNTREND":

        recommendation = "REDUCE"

    else:

        recommendation = "HOLD"

    return {

        "recommendation": recommendation,

        "advice": advice
    }