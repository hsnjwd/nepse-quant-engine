def volume_score(latest):

    score = 0

    volume_signal = "Normal"

    rvol = latest.get("RVOL", 1)

    if rvol >= 2:

        score += 2
        volume_signal = "Very High"

    elif rvol >= 1.5:

        score += 1
        volume_signal = "High"

    elif rvol <= 0.5:

        score -= 1
        volume_signal = "Low"

    return {
        "score": score,
        "signal": volume_signal,
        "rvol": round(float(rvol), 2),
    }