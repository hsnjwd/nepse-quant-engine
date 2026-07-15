def calculate_confidence(
        score,
        market,
        volume_signal
):

    confidence = 50


    # Technical score
    confidence += score * 5


    # Market trend
    if market == "BULLISH":
        confidence += 10

    elif market == "BEARISH":
        confidence -= 10


    # Volume confirmation
    if volume_signal == "HIGH_VOLUME":
        confidence += 10


    if confidence > 95:
        confidence = 95


    if confidence < 10:
        confidence = 10


    return confidence