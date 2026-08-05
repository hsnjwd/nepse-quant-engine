import requests

from src.config import API_BASE_URL, TELEGRAM_TOKEN
from src.logging.logger import logger

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# -----------------------------
# Configuration (single source: src.config)
# -----------------------------

API_BASE = API_BASE_URL

# -----------------------------
# Commands
# -----------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "📈 NEPSE Quant Bot Online\n\n"
        "Available Commands:\n"
        "/analyze SYMBOL\n"
        "/top10\n"
        "/buylist\n"
        "/selllist\n"
        "/market\n"
        "/help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await start(update, context)


# -----------------------------
# Analyze Stock
# -----------------------------


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage:\n/analyze NABIL"
        )
        return

    symbol = context.args[0].lower()

    try:

        response = requests.get(
            f"{API_BASE}/analyze/{symbol}",
            timeout=10,
        )

        if response.status_code != 200:

            await update.message.reply_text(
                "Stock not found."
            )
            return

        data = response.json()

        emoji = {
            "BUY": "🟢",
            "HOLD": "🟡",
            "SELL": "🔴"
        }.get(data["signal"], "⚪")

        buy_zone = (
            f"Rs. {data['buy_zone'][0]:.2f} - "
            f"Rs. {data['buy_zone'][1]:.2f}"
            if data["buy_zone"][0] is not None
            else "N/A"
        )

        stop_loss = (
            f"Rs. {data['stop_loss']:.2f}"
            if data["stop_loss"] is not None
            else "N/A"
        )

        target1 = (
            f"Rs. {data['target1']:.2f}"
            if data["target1"] is not None
            else "N/A"
        )

        target2 = (
            f"Rs. {data['target2']:.2f}"
            if data["target2"] is not None
            else "N/A"
        )

        target3 = (
            f"Rs. {data['target3']:.2f}"
            if data["target3"] is not None
            else "N/A"
        )

        message = (
            f"📊 {data['symbol']}\n\n"

            f"{emoji} Signal: {data['signal']}\n"
            f"🎯 Confidence: {data['confidence']}%\n\n"

            f"💰 Current Price\n"
            f"Rs. {data['price']:.2f}\n\n"

            f"🛒 Buy Zone\n"
            f"{buy_zone}\n\n"

            f"🛑 Stop Loss\n"
            f"{stop_loss}\n\n"

            f"🎯 Target 1\n"
            f"{target1}\n"

            f"🎯 Target 2\n"
            f"{target2}\n"

            f"🎯 Target 3\n"
            f"{target3}\n\n"

            f"📊 Score : {data['score']}\n"
            f"📉 RSI : {data['rsi']:.2f}\n"
            f"📈 MACD : {data['macd']:.4f}"
        )

        await update.message.reply_text(message)

    except Exception:
        logger.exception("Error processing /analyze command")

        await update.message.reply_text(
            "Error processing request."
        )


# -----------------------------
# Top 10
# -----------------------------


async def top10(update: Update, context: ContextTypes.DEFAULT_TYPE):

    data = requests.get(
        f"{API_BASE}/market/top10"

    ).json()

    text = "🏆 Top 10 Stocks\n\n"

    for i, stock in enumerate(data, start=1):

        text += (
            f"{i}. {stock['symbol']}"
            f" | {stock['signal']}"
            f" | Score {stock['score']}\n"
        )

    await update.message.reply_text(text)


# -----------------------------
# Buy List
# -----------------------------


async def buylist(update: Update, context: ContextTypes.DEFAULT_TYPE):

    response = requests.get(
    f"{API_BASE}/market/buylist",
    timeout=10,
)

    response.raise_for_status()

    data = response.json()

    if not data:

        await update.message.reply_text(
            "No BUY signals today."
        )
        return

    text = "🟢 BUY Signals\n\n"

    for stock in data:

        text += (
            f"{stock['symbol']} "
            f"(Score {stock['score']})\n"
        )

    await update.message.reply_text(text)


# -----------------------------
# Sell List
# -----------------------------


async def selllist(update: Update, context: ContextTypes.DEFAULT_TYPE):

    response = requests.get(
    f"{API_BASE}/market/selllist",
    timeout=10,
)

    response.raise_for_status()

    data = response.json()

    if not data:

        await update.message.reply_text(
            "No SELL signals today."
        )
        return

    text = "🔴 SELL Signals\n\n"

    for stock in data:

        text += (
            f"{stock['symbol']} "
            f"(Score {stock['score']})\n"
        )

    await update.message.reply_text(text)


# -----------------------------
# Market Summary
# -----------------------------


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        response = requests.get(
            f"{API_BASE}/market/",
            timeout=10,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException:
        logger.exception("Unable to fetch market summary")
        await update.message.reply_text(
            "Unable to fetch market summary."
        )
        return

    message = (
        "📊 NEPSE Market Summary\n\n"
        f"📈 Analyzed : {data['total']}\n"
        f"⏭ Skipped  : {data['skipped']}\n\n"
        f"🟢 BUY  : {data['buy']}\n"
        f"🟡 HOLD : {data['hold']}\n"
        f"🔴 SELL : {data['sell']}"
    )

    await update.message.reply_text(message)


# -----------------------------
# Main
# -----------------------------


def main():

    if not TELEGRAM_TOKEN:
        raise ValueError(
            "TELEGRAM_TOKEN not found in .env"
        )

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("top10", top10))
    app.add_handler(CommandHandler("buylist", buylist))
    app.add_handler(CommandHandler("selllist", selllist))
    app.add_handler(CommandHandler("market", market))

    logger.info("🚀 NEPSE Quant Engine Telegram Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()