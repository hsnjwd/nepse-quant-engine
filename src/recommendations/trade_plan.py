def create_trade_plan(result):
    """
    Generate a complete trade plan.
    """

    price = result["price"]
    atr = result["atr"]
    if price is None:
        raise ValueError("Missing price")

    if atr is None or atr <= 0:
        atr = price * 0.03
    signal = result["signal"]
    confidence = result["confidence"]

    # ----------------------
    # Entry Zone
    # ----------------------

    entry_low = round(price - atr * 0.25, 2)
    entry_high = round(price + atr * 0.25, 2)

    # ----------------------
    # Stop Loss
    # ----------------------

    stop = round(price - atr, 2)

    # ----------------------
    # Targets
    # ----------------------

    t1 = round(price + atr, 2)
    t2 = round(price + atr * 2, 2)
    t3 = round(price + atr * 3, 2)

    # ----------------------
    # Risk Level
    # ----------------------

    if confidence >= 85:
        risk = "LOW"

    elif confidence >= 70:
        risk = "MEDIUM"

    else:
        risk = "HIGH"

    # ----------------------
    # Holding Period
    # ----------------------

    if signal == "BUY":
        holding = "3–10 Days"

    elif signal == "HOLD":
        holding = "Monitor"

    else:
        holding = "Exit / Avoid"

    # ----------------------
    # Trade Style
    # ----------------------

    if result["trend"] == "UPTREND":
        style = "Trend Following"

    elif result["trend"] == "SIDEWAYS":
        style = "Swing Trade"

    else:
        style = "Counter Trend"

    return {

        "entry_zone": f"{entry_low} - {entry_high}",

        "stop_loss": stop,

        "target1": t1,

        "target2": t2,

        "target3": t3,

        "risk": risk,

        "holding_period": holding,

        "trade_style": style
    }