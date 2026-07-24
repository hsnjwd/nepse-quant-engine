from src.cache.market_cache import get_market_scan
from src.services.market_filters import (
    filter_signal,
    filter_confidence,
    filter_score,
    filter_rr,
    sort_confidence,

)

def get_top10():

    market = get_market_scan()

    return market["results"][:10]


def get_buy_list():

    market = get_market_scan()

    return [
        stock
        for stock in market["results"]
        if stock["signal"] == "BUY"
    ]


def get_sell_list():

    market = get_market_scan()

    return [
        stock
        for stock in market["results"]
        if stock["signal"] == "SELL"
    ]


def get_market_summary():

    market = get_market_scan()

    stocks = market["results"]

    buys = sum(
        1
        for stock in stocks
        if stock["signal"] == "BUY"
    )

    holds = sum(
        1
        for stock in stocks
        if stock["signal"] == "HOLD"
    )

    sells = sum(
        1
        for stock in stocks
        if stock["signal"] == "SELL"
    )

    return {
        "total": len(stocks),
        "buy": buys,
        "hold": holds,
        "sell": sells,
        "skipped": len(market["skipped"]),
        "top10": stocks[:10],
    }

def get_strong_buy_list():

    market = get_market_scan()

    stocks = market["results"]

    stocks = filter_signal(stocks, "BUY")

    stocks = filter_confidence(stocks, 90)

    stocks = filter_score(stocks, 5)

    stocks = filter_rr(stocks, 2.5)

    stocks = sort_confidence(stocks)

    return stocks    