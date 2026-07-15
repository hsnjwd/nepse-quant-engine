import os

from dotenv import load_dotenv

load_dotenv()

# ==============================
# Telegram
# ==============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ==============================
# API
# ==============================

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://127.0.0.1:8000"
)

# ==============================
# Data
# ==============================

DATA_DIRECTORY = os.getenv(
    "DATA_DIRECTORY",
    "data/raw"
)

# ==============================
# Indicator Settings
# ==============================

RSI_PERIOD = int(
    os.getenv("RSI_PERIOD", 14)
)

MACD_FAST = int(
    os.getenv("MACD_FAST", 12)
)

MACD_SLOW = int(
    os.getenv("MACD_SLOW", 26)
)

MACD_SIGNAL = int(
    os.getenv("MACD_SIGNAL", 9)
)