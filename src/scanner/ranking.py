def rank_market(stocks):

    def ranking_score(stock):

        score = 0

        score += stock["score"] * 10

        score += stock["confidence"]

        score += stock["best_rr"] * 20

        if stock["signal"] == "BUY":
            score += 100

        elif stock["signal"] == "HOLD":
            score += 20

        elif stock["signal"] == "SELL":
            score -= 100

        return score

    stocks.sort(
        key=ranking_score,
        reverse=True
    )

    return stocks