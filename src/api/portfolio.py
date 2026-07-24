from fastapi import APIRouter

from src.portfolio.analyzer import analyze_portfolio

router = APIRouter()


@router.get("/")
def portfolio():

    return analyze_portfolio()