def calculate_score(row, pattern):
    """
    Calculate technical score and explain it.
    """

    score = 0

    breakdown = {
        "trend": 0,
        "rsi": 0,
        "macd": 0,
        "volume": 0,
        "pattern": 0,
        "reasons": []
    }

    # -----------------------
    # Trend
    # -----------------------

    if row["Close"] > row["SMA_20"]:
        score += 1
        breakdown["trend"] += 1
        breakdown["reasons"].append("Above SMA20")
    else:
        score -= 1
        breakdown["trend"] -= 1
        breakdown["reasons"].append("Below SMA20")

    if row["Close"] > row["SMA_50"]:
        score += 1
        breakdown["trend"] += 1
        breakdown["reasons"].append("Above SMA50")
    else:
        score -= 1
        breakdown["trend"] -= 1
        breakdown["reasons"].append("Below SMA50")

    # -----------------------
    # RSI
    # -----------------------

    if row["RSI"] < 35:
        score += 2
        breakdown["rsi"] += 2
        breakdown["reasons"].append("RSI Oversold")

    elif row["RSI"] > 70:
        score -= 2
        breakdown["rsi"] -= 2
        breakdown["reasons"].append("RSI Overbought")

    elif 40 <= row["RSI"] <= 60:
        score += 1
        breakdown["rsi"] += 1
        breakdown["reasons"].append("Healthy RSI")

    # -----------------------
    # MACD
    # -----------------------

    if row["MACD"] > row["MACD_SIGNAL"]:
        score += 2
        breakdown["macd"] += 2
        breakdown["reasons"].append("Bullish MACD")
    else:
        score -= 2
        breakdown["macd"] -= 2
        breakdown["reasons"].append("Bearish MACD")

    # -----------------------
    # Volume
    # -----------------------

    volume_points = int(row["VOLUME_SCORE"])

    score += volume_points

    breakdown["volume"] = volume_points

    if volume_points > 0:
        breakdown["reasons"].append("Strong Volume")

    elif volume_points < 0:
        breakdown["reasons"].append("Weak Volume")

    # -----------------------
    # Candlestick Pattern
    # -----------------------

    pattern_score = pattern["score"]

    score += pattern_score

    breakdown["pattern"] = pattern_score

    if pattern_score > 0:
        breakdown["reasons"].append(pattern["name"])

    elif pattern_score < 0:
        breakdown["reasons"].append(pattern["name"])

    return score, breakdown
