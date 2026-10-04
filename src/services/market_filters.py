def filter_signal(stocks, signal):

    return [
        stock
        for stock in stocks
        if stock["signal"] == signal
    ]


def filter_confidence(stocks, minimum):

    return [
        stock
        for stock in stocks
        if stock["confidence"] >= minimum
    ]


def filter_score(stocks, minimum):

    return [
        stock
        for stock in stocks
        if stock["score"] >= minimum
    ]


def filter_rr(stocks, minimum):

    return [
        stock
        for stock in stocks
        if stock["best_rr"] >= minimum
    ]


def sort_confidence(stocks):

    return sorted(
        stocks,
        key=lambda x: x["confidence"],
        reverse=True
    )


def sort_score(stocks):

    return sorted(
        stocks,
        key=lambda x: x["score"],
        reverse=True
    )


def sort_rr(stocks):

    return sorted(
        stocks,
        key=lambda x: x["best_rr"],
        reverse=True
    )