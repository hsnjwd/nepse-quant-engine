from fastapi import APIRouter

from src.services.market_service import (
    get_top10,
    get_buy_list,
    get_sell_list,
    get_market_summary,
    get_strong_buy_list,
)

router = APIRouter()


@router.get("/")
def market():
    return get_market_summary()


@router.get("/top10")
def top10():
    return get_top10()


@router.get("/buylist")
def buylist():
    return get_buy_list()


@router.get("/selllist")
def selllist():
    return get_sell_list()


@router.get("/strongbuy")
def strongbuy():
    return get_strong_buy_list()   