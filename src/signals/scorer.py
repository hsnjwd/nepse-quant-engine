def calculate_score(row):
    """
    Calculate stock technical score
    Range: -10 to +10
    """

    score = 0


    # Trend
    if row["Close"] > row["SMA_20"]:
        score += 1
    else:
        score -= 1


    if row["Close"] > row["SMA_50"]:
        score += 1
    else:
        score -= 1


    # RSI
    if row["RSI"] < 35:
        score += 2

    elif row["RSI"] > 70:
        score -= 2

    elif 40 <= row["RSI"] <= 60:
        score += 1


    # MACD
    if row["MACD"] > row["MACD_SIGNAL"]:
        score += 2
    else:
        score -= 2


    # Volume
    if row["VOLUME_SIGNAL"] == "HIGH_VOLUME":
        score += 2

    elif row["VOLUME_SIGNAL"] == "LOW_VOLUME":
        score -= 1


    return score



def generate_signal(score):

    if score >= 5:
        return "BUY"

    elif score <= -3:
        return "SELL"

    else:
        return "HOLD"