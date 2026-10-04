def check_alerts(result):
    """
    Generate alerts from a stock analysis result.
    """

    alerts = []

    signal = result.get("signal")
    confidence = result.get("confidence", 0)
    score = result.get("score", 0)
    rr = result.get("best_rr", 0)

    # BUY Alert
    if signal == "BUY":
        alerts.append({
            "type": "BUY",
            "priority": 5,
            "message": "BUY signal generated."
        })

    # SELL Alert
    if signal == "SELL":
        alerts.append({
            "type": "SELL",
            "priority": 5,
            "message": "SELL signal generated."
        })

    # High Confidence
    if confidence >= 90:
        alerts.append({
            "type": "HIGH_CONFIDENCE",
            "priority": 4,
            "message": f"Confidence is {confidence}%."
        })

    # Excellent Risk Reward
    if rr >= 3:
        alerts.append({
            "type": "HIGH_RR",
            "priority": 4,
            "message": f"Risk/Reward = {rr:.1f}"
        })

    # Strong Volume
    if result.get("volume_signal") == "VOLUME_SPIKE":
        alerts.append({
            "type": "VOLUME_SPIKE",
            "priority": 3,
            "message": "Volume spike detected."
        })

    # Bullish Pattern
    if result.get("pattern_type") == "Bullish":
        alerts.append({
            "type": "BULLISH_PATTERN",
            "priority": 3,
            "message": result.get("pattern")
        })

    # Bearish Pattern
    if result.get("pattern_type") == "Bearish":
        alerts.append({
            "type": "BEARISH_PATTERN",
            "priority": 3,
            "message": result.get("pattern")
        })

    # Trend
    trend = result.get("trend")

    if trend == "UPTREND":
        alerts.append({
            "type": "UPTREND",
            "priority": 2,
            "message": "Strong trend."
        })

    if trend == "DOWNTREND":
        alerts.append({
            "type": "DOWNTREND",
            "priority": 2,
            "message": "Downtrend."
        })

    return alerts


def check_signal_alerts(previous, result):

    alerts = []

    if previous["signal"] != result["signal"]:

        alerts.append({
            "type": "SIGNAL_CHANGE",
            "priority": 5,
            "message": (
                f"{previous['signal']} → {result['signal']}"
            )
        })

    return alerts


def check_confidence_alerts(previous, result):

    alerts = []

    if (
        previous["confidence"] < 90
        and result["confidence"] >= 90
    ):

        alerts.append({
            "type": "CONFIDENCE_UPGRADE",
            "priority": 4,
            "message": (
                f"Confidence increased to "
                f"{result['confidence']}%"
            )
        })

    elif (
        previous["confidence"] >= 90
        and result["confidence"] < 90
    ):

        alerts.append({
            "type": "CONFIDENCE_DROP",
            "priority": 4,
            "message": (
                f"Confidence dropped to "
                f"{result['confidence']}%"
            )
        })

    return alerts


def check_score_alerts(previous, result):

    alerts = []

    score_change = result["score"] - previous["score"]

    if score_change >= 2:

        alerts.append({
            "type": "SCORE_UPGRADE",
            "priority": 3,
            "message": (
                f"Score improved "
                f"{previous['score']} → {result['score']}"
            )
        })

    elif score_change <= -2:

        alerts.append({
            "type": "SCORE_DOWNGRADE",
            "priority": 3,
            "message": (
                f"Score dropped "
                f"{previous['score']} → {result['score']}"
            )
        })

    return alerts


def check_trend_alerts(previous, result):

    alerts = []

    if previous["trend"] != result["trend"]:

        alerts.append({
            "type": "TREND_CHANGE",
            "priority": 4,
            "message": (
                f"Trend changed "
                f"{previous['trend']} → {result['trend']}"
            )
        })

    return alerts


def check_volume_alerts(previous, result):

    alerts = []

    if (
        previous["volume_signal"] != "VOLUME_SPIKE"
        and result["volume_signal"] == "VOLUME_SPIKE"
    ):

        alerts.append({
            "type": "VOLUME_SPIKE",
            "priority": 3,
            "message": (
                f"Volume spike detected "
                f"({result['relative_volume']:.2f}x average)"
            )
        })


    elif (
        previous["volume_signal"] == "VOLUME_SPIKE"
        and result["volume_signal"] != "VOLUME_SPIKE"
    ):

        alerts.append({
            "type": "VOLUME_NORMAL",
            "priority": 2,
            "message": "Volume returned to normal."
        })

    return alerts

def check_target_alerts(previous, result):

    alerts = []

    milestones = previous["milestones"]

    price = result["price"]

    if (
        not milestones["target1"]
        and price >= result["target1"]
    ):

        alerts.append({
            "type": "TARGET1",
            "priority": 5,
            "message": (
                f"Target 1 reached ({result['target1']})"
            )
        })

        milestones["target1"] = True

    if (
        not milestones["target2"]
        and price >= result["target2"]
    ):

        alerts.append({
            "type": "TARGET2",
            "priority": 5,
            "message": (
                f"Target 2 reached ({result['target2']})"
            )
        })

        milestones["target2"] = True

    if (
        not milestones["target3"]
        and price >= result["target3"]
    ):

        alerts.append({
            "type": "TARGET3",
            "priority": 5,
            "message": (
                f"Target 3 reached ({result['target3']})"
            )
        })

        milestones["target3"] = True

    result["milestones"] = milestones

    return alerts