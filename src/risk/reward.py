def calculate_risk_reward(result):
    """
    Calculate Risk/Reward ratios.
    """

    price = result["price"]
    stop = result["stop_loss"]

    t1 = result["target1"]
    t2 = result["target2"]
    t3 = result["target3"]

    risk = abs(price - stop)

    if risk == 0:
        risk = 0.01

    rr1 = round(abs(t1 - price) / risk, 2)
    rr2 = round(abs(t2 - price) / risk, 2)
    rr3 = round(abs(t3 - price) / risk, 2)

    best_rr = max(rr1, rr2, rr3)

    if best_rr >= 3:
        grade = "EXCELLENT"
    elif best_rr >= 2:
        grade = "GOOD"
    elif best_rr >= 1.5:
        grade = "FAIR"
    else:
        grade = "POOR"

    return {
        "risk_amount": round(risk, 2),
        "rr_target1": rr1,
        "rr_target2": rr2,
        "rr_target3": rr3,
        "best_rr": best_rr,
        "rr_grade": grade,
    }