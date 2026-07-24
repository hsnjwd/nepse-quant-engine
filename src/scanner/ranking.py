TECHNICAL_WEIGHT = 0.30
CONFIDENCE_WEIGHT = 0.25
RR_WEIGHT = 0.15
VOLUME_WEIGHT = 0.10
TREND_WEIGHT = 0.10
PATTERN_WEIGHT = 0.10

UPTREND = 100
SIDEWAYS = 50
DOWNTREND = 0


def _normalize_metric(value, minimum, maximum):
    if value is None:
        return 0.0

    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0

    if maximum == minimum:
        return 50.0

    normalized = (value - minimum) / (maximum - minimum) * 100.0
    return max(0.0, min(100.0, normalized))


def _get_pattern_strength_score(stock):
    raw_strength = stock.get("pattern_strength")

    if raw_strength is None:
        return 0.0

    if isinstance(raw_strength, str):
        value = raw_strength.strip().lower()
        mapping = {
            "weak": 25.0,
            "moderate": 50.0,
            "strong": 75.0,
            "very strong": 100.0,
            "very_strong": 100.0,
        }
        return mapping.get(value, 50.0)

    try:
        value = float(raw_strength)
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(100.0, value))


def calculate_rank(stock):
    score_value = stock.get("normalized_score")
    if score_value is None:
        score_value = _normalize_metric(stock.get("score", 0), -10, 10)

    confidence_value = stock.get("normalized_confidence")
    if confidence_value is None:
        confidence_value = _normalize_metric(stock.get("confidence", 0), 0, 100)

    rr_value = stock.get("normalized_rr")
    if rr_value is None:
        rr_value = _normalize_metric(stock.get("best_rr", 0), 0, 5)

    volume_value = stock.get("normalized_volume")
    if volume_value is None:
        volume_value = _normalize_metric(stock.get("relative_volume", 0), 0, 5)

    trend_value = stock.get("normalized_trend")
    if trend_value is None:
        trend_name = stock.get("trend", "SIDEWAYS")
        trend_value = {
            "UPTREND": UPTREND,
            "SIDEWAYS": SIDEWAYS,
            "DOWNTREND": DOWNTREND,
        }.get(trend_name.upper(), SIDEWAYS)

    pattern_value = stock.get("normalized_pattern")
    if pattern_value is None:
        pattern_value = _get_pattern_strength_score(stock)

    signal_bonus = 0.0
    signal = str(stock.get("signal", "HOLD")).upper()
    if signal == "BUY":
        signal_bonus = 100.0
    elif signal == "SELL":
        signal_bonus = -100.0

    composite_rank = (
        score_value * TECHNICAL_WEIGHT
        + confidence_value * CONFIDENCE_WEIGHT
        + rr_value * RR_WEIGHT
        + volume_value * VOLUME_WEIGHT
        + trend_value * TREND_WEIGHT / 100.0
        + pattern_value * PATTERN_WEIGHT / 100.0
        + signal_bonus * 0.01
    )

    stock["composite_rank"] = composite_rank
    return composite_rank


def rank_market(stocks):
    if not stocks:
        return stocks

    scores = [stock.get("score", 0) for stock in stocks]
    confidences = [stock.get("confidence", 0) for stock in stocks]
    rr_values = [stock.get("best_rr", 0) for stock in stocks]
    volume_values = [stock.get("relative_volume", 0) for stock in stocks]

    min_score = min(scores) if scores else -10
    max_score = max(scores) if scores else 10
    min_confidence = min(confidences) if confidences else 0
    max_confidence = max(confidences) if confidences else 100
    min_rr = min(rr_values) if rr_values else 0
    max_rr = max(rr_values) if rr_values else 5
    min_volume = min(volume_values) if volume_values else 0
    max_volume = max(volume_values) if volume_values else 5

    for stock in stocks:
        stock["normalized_score"] = _normalize_metric(stock.get("score", 0), min_score, max_score)
        stock["normalized_confidence"] = _normalize_metric(stock.get("confidence", 0), min_confidence, max_confidence)
        stock["normalized_rr"] = _normalize_metric(stock.get("best_rr", 0), min_rr, max_rr)
        stock["normalized_volume"] = _normalize_metric(stock.get("relative_volume", 0), min_volume, max_volume)
        stock["normalized_trend"] = {
            "UPTREND": UPTREND,
            "SIDEWAYS": SIDEWAYS,
            "DOWNTREND": DOWNTREND,
        }.get(str(stock.get("trend", "SIDEWAYS")).upper(), SIDEWAYS)
        stock["normalized_pattern"] = _get_pattern_strength_score(stock)

    for stock in stocks:
        calculate_rank(stock)

    stocks.sort(key=lambda stock: stock["composite_rank"], reverse=True)

    for index, stock in enumerate(stocks, start=1):
        stock["rank"] = index

    return stocks