from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def home():

    return {
        "status": "NEPSE Quant Engine Running"
    }