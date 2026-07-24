from fastapi import APIRouter

from src.watchlist.manager import (
    add_stock,
    remove_stock,
    load_watchlist,
)

from src.watchlist.scanner import scan_watchlist


router = APIRouter(
    prefix="/watchlist",
    tags=["Watchlist"],
)


@router.get("")
def get_watchlist():
    return load_watchlist()


@router.post("/add/{symbol}")
def add(symbol: str):

    add_stock(symbol.upper())

    return {
        "status": "added",
        "symbol": symbol.upper()
    }


@router.delete("/remove/{symbol}")
def remove(symbol: str):

    remove_stock(symbol.upper())

    return {
        "status": "removed",
        "symbol": symbol.upper()
    }


@router.get("/scan")
def scan():

    return scan_watchlist()