def calculate_atr_stop_loss(price, atr, direction="BUY"):

    if direction == "BUY":
        return round(price - (2 * atr), 2)

    else:
        return round(price + (2 * atr), 2)



def calculate_targets(price, atr):

    return {
        "T1": round(price + (1.5 * atr), 2),
        "T2": round(price + (3 * atr), 2),
        "T3": round(price + (5 * atr), 2)
    }



def calculate_risk_reward(entry, target, stop_loss):

    risk = entry - stop_loss

    reward = target - entry

    if risk <= 0:
        return 0

    return round(
        reward / risk,
        2
    )