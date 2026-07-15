import os
import requests

from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
load_dotenv()

# Base URL of your FastAPI server
API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://127.0.0.1:8000"
)

TOKEN = os.getenv("TELEGRAM_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "📈 NEPSE Quant Bot Online\n\n"
        "Available Commands:\n"
        "/start - Start the bot\n"
        "/help - Show help\n"
        "/analyze <symbol>\n\n"
        "Examples:\n"
        "/analyze nabil\n"
        "/analyze api\n"
        "/analyze gbime"
    )

    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage:\n"
            "/analyze <symbol>\n\n"
            "Example:\n"
            "/analyze nabil"
        )
        return

    symbol = context.args[0].lower()
    display_symbol = symbol.upper()

    try:

        response = requests.get(
            f"{API_BASE_URL}/analyze/{symbol}",
            timeout=10,
        )

        if response.status_code == 404:
            await update.message.reply_text(
                f"❌ No CSV found for {display_symbol}"
            )
            return

        response.raise_for_status()

        data = response.json()

        price = data.get("price")
        score = data.get("score")
        signal = data.get("signal")
        rsi = data.get("rsi")
        macd = data.get("macd")

        message = (
            f"📊 {display_symbol}\n\n"
            f"💰 Price: Rs. {price:.2f}\n"
            f"📈 Signal: {signal}\n"
            f"📊 Score: {score}\n"
            f"📉 RSI: {rsi:.2f}\n"
            f"📈 MACD: {macd:.4f}"
        )

        await update.message.reply_text(message)

    except requests.exceptions.ConnectionError:
        await update.message.reply_text(
            "❌ Cannot connect to the Quant Engine.\n"
            "Make sure FastAPI is running."
        )

    except requests.exceptions.Timeout:
        await update.message.reply_text(
            "❌ Request timed out."
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Error:\n{e}"
        )


def main():

    if not TOKEN:
        raise ValueError(
            "TELEGRAM_TOKEN not found in .env"
        )

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("analyze", analyze))

    print("🚀 Telegram Bot Running...")

    app.run_polling()


if __name__ == "__main__":
    main()