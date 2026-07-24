def generate_signal(result):
    """
    Decision Engine v2
    """

    score = result["score"]
    trend = result["trend"]
    rsi = result["rsi"]
    volume = result["volume_signal"]

    macd_bullish = (
        result["score_breakdown"]["macd"] > 0
    )

    above_sma = (
        result["score_breakdown"]["trend"] >= 0
    )

    # --------------------
    # BUY
    # --------------------

    if (
        score >= 5
        and trend != "DOWNTREND"
        and macd_bullish
        and 35 <= rsi <= 70
        and volume != "LOW_VOLUME"
        and above_sma
    ):
        return "BUY"

    # --------------------
    # SELL
    # --------------------

    if (
        score <= -3
        and trend == "DOWNTREND"
        and not macd_bullish
    ):
        return "SELL"

    return "HOLD"