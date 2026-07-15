def calculate_risk_reward(entry, target, stop_loss):

    risk = entry - stop_loss

    reward = target - entry


    if risk <= 0:
        return 0


    return round(reward / risk, 2)