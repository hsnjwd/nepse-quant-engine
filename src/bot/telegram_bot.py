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
        "/backtest SYMBOL\n"
        "/signals SYMBOL\n"
        "/top10\n"
        "/buylist\n"
        "/selllist\n"
        "/strongbuy\n"
        "/market\n"
        "/watchlist\n"
        "/watchlist scan\n"
        "/portfolio\n"
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

        # ``entry_zone`` is the field produced by ``create_trade_plan``
        # (a formatted string like ``"999.00 - 1001.00"``).  The legacy
        # ``buy_zone`` list no longer exists in the /analyze payload —
        # indexing it raised KeyError, which the broad ``except`` turned
        # into "Error processing request." for EVERY /analyze command
        # (caught by the production-gate bot section).
        buy_zone = data.get("entry_zone") or "N/A"

        stop_loss = (
            f"Rs. {data['stop_loss']:.2f}"
            if data.get("stop_loss") is not None
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
            if data.get("target3") is not None
            else "N/A"
        )

        # safe_float() yields None for NaN indicators (e.g. a degenerate
        # volume series), and ``None`` cannot be format-spec'd — guard the
        # numeric fields the same way the price/target fields above are.
        price = data.get("price")
        price_text = f"Rs. {price:.2f}" if isinstance(price, (int, float)) else "N/A"
        rsi = data.get("rsi")
        rsi_text = f"{rsi:.2f}" if isinstance(rsi, (int, float)) else "N/A"
        macd = data.get("macd")
        macd_text = f"{macd:.4f}" if isinstance(macd, (int, float)) else "N/A"

        message = (
            f"📊 {data['symbol']}\n\n"

            f"{emoji} Signal: {data['signal']}\n"
            f"🎯 Confidence: {data['confidence']}%\n\n"

            f"💰 Current Price\n"
            f"{price_text}\n\n"

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
            f"📉 RSI : {rsi_text}\n"
            f"📈 MACD : {macd_text}"
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
        f"{API_BASE}/market/top10",
        timeout=60,
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
    timeout=60,
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
    timeout=60,
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
        # timeout=60: the first /market-family call on a COLD cache
        # scans the whole corpus server-side (measured 13.8 s for 286
        # symbols on the live corpus, 2026-08-12) — a 10 s read timeout
        # made /market reply "Unable to fetch market summary." for a
        # cold-cache user while the scan kept running.  Warm-cache
        # calls return in ~0.1 s regardless.
        response = requests.get(
            f"{API_BASE}/market/",
            timeout=60,
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
# Strong Buy List
# -----------------------------


async def strongbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        response = requests.get(
            f"{API_BASE}/market/strongbuy",
            timeout=60,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException:
        logger.exception("Unable to fetch strong buy list")
        await update.message.reply_text(
            "Unable to fetch strong buy list."
        )
        return

    if not data:

        await update.message.reply_text(
            "No STRONG BUY signals today."
        )
        return

    text = "💪 STRONG BUY Signals\n\n"

    for stock in data:

        text += (
            f"{stock['symbol']} "
            f"(Score {stock['score']}, "
            f"Conf {stock['confidence']}%)\n"
        )

    await update.message.reply_text(text)


# -----------------------------
# Watchlist (list or scan)
# -----------------------------


async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # ``/watchlist scan`` is a sub-command of ``/watchlist`` (Telegram
    # commands are single tokens, so the argument distinguishes the two):
    # the scanner analyses every enabled watched symbol and returns
    # per-symbol signals + new alerts.
    if context.args and context.args[0].lower() == "scan":
        await _watchlist_scan(update, context)
        return

    try:
        response = requests.get(
            f"{API_BASE}/watchlist",
            timeout=10,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException:
        logger.exception("Unable to fetch watchlist")
        await update.message.reply_text(
            "Unable to fetch watchlist."
        )
        return

    if not data:

        await update.message.reply_text(
            "Watchlist is empty."
        )
        return

    text = "📋 Watchlist\n\n"

    for symbol in sorted(data):

        enabled = data[symbol].get("enabled", True)

        text += (
            f"{'✅' if enabled else '⏸️'} {symbol}\n"
        )

    await update.message.reply_text(text)


async def _watchlist_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        response = requests.get(
            f"{API_BASE}/watchlist/scan",
            timeout=60,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException:
        logger.exception("Unable to scan watchlist")
        await update.message.reply_text(
            "Unable to scan watchlist."
        )
        return

    scanned = data.get("stocks_scanned", 0)

    if scanned == 0:

        await update.message.reply_text(
            "Watchlist is empty — add symbols first."
        )
        return

    text = "🔍 Watchlist Scan\n\n"

    for result in data.get("results", []):

        if result.get("error"):

            text += f"⚠️ {result['symbol']} — {result['error']}\n"
            continue

        text += (
            f"📊 {result['symbol']} "
            f"| {result.get('signal', 'N/A')} "
            f"| Score {result.get('score', 'N/A')}\n"
        )

    alerts = data.get("alerts", [])

    if alerts:

        text += f"\n🚨 {len(alerts)} symbol(s) with new alerts\n"

    await update.message.reply_text(text)


# -----------------------------
# Portfolio
# -----------------------------


async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        response = requests.get(
            f"{API_BASE}/portfolio/",
            timeout=10,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException:
        logger.exception("Unable to fetch portfolio")
        await update.message.reply_text(
            "Unable to fetch portfolio."
        )
        return

    pnl = data.get("portfolio_pnl", 0)

    pnl_emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")

    message = (
        "💼 Portfolio Summary\n\n"
        f"💰 Portfolio Value  : Rs. {data.get('portfolio_value', 0):,.2f}\n"
        f"📥 Cost             : Rs. {data.get('portfolio_cost', 0):,.2f}\n"
        f"{pnl_emoji} PnL              : Rs. {pnl:,.2f}\n"
        f"📈 Return           : {data.get('portfolio_return_pct', 0):.2f}%\n"
        f"📦 Holdings         : {len(data.get('holdings', []))}"
    )

    await update.message.reply_text(message)


# -----------------------------
# Backtest
# -----------------------------


async def backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage:\n/backtest NABIL"
        )
        return

    symbol = context.args[0].lower()

    try:
        response = requests.get(
            f"{API_BASE}/backtest/{symbol}",
            timeout=60,
        )

        if response.status_code == 404:

            await update.message.reply_text(
                "Stock not found."
            )
            return

        response.raise_for_status()

        data = response.json()

    except requests.RequestException:
        logger.exception("Unable to run backtest")
        await update.message.reply_text(
            "Unable to run backtest."
        )
        return

    # The /backtest payload carries per-trade ``return_pct`` values but
    # not the compiled headline ratios — compute them with the engine's
    # own pure metric helpers (same numerics the engine uses elsewhere,
    # not a re-implementation in the bot).
    from src.backtest.metrics import (  # noqa: PLC0415
        calculate_max_drawdown,
        calculate_sharpe_ratio,
        calculate_total_return,
    )

    trades = data.get("trades") or []
    returns = [
        float(t.get("return_pct", 0.0) or 0.0)
        for t in trades
    ]
    equity = [100.0]
    for ret in returns:
        equity.append(equity[-1] * (1.0 + ret / 100.0))

    total_return = calculate_total_return(100.0, equity[-1])
    sharpe = calculate_sharpe_ratio(returns)
    max_drawdown = calculate_max_drawdown(equity)

    metrics = data.get("metrics") or {}
    total_trades = int(data.get("total_trades", len(trades)))
    candles = int(data.get("candles", 0))

    message = (
        f"📈 Backtest: {symbol.upper()}\n\n"
        f"🕯 Candles  : {candles}\n"
        f"🔁 Trades   : {total_trades}\n\n"
        f"💰 Return      : {total_return:+.2f}%\n"
        f"📉 Sharpe      : {sharpe:.2f}\n"
        f"🛑 Max Drawdown: {max_drawdown:.2f}%\n\n"
        f"✅ Win Rate  : {float(metrics.get('win_rate', 0.0)):.2f}%\n"
        f"📊 Profit F. : {float(metrics.get('profit_factor', 0.0)):.2f}\n"
        f"🎯 Expectancy: {float(metrics.get('expectancy', 0.0)):+.2f}%"
    )

    if total_trades == 0:
        message += "\n\nNo trades generated — no BUY signals matched."

    await update.message.reply_text(message)


# -----------------------------
# Signal Explanation
# -----------------------------


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage:\n/signals NABIL"
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

        analysis = response.json()

        explain_response = requests.post(
            f"{API_BASE}/signals/explain",
            json={
                "symbol": symbol,
                "analysis": analysis,
                "signal": analysis.get("signal"),
                "confidence": analysis.get("confidence"),
            },
            timeout=10,
        )

        explain_response.raise_for_status()

        explanation = (explain_response.json() or {}).get("data") or {}

    except requests.RequestException:
        logger.exception("Unable to explain signal")
        await update.message.reply_text(
            "Unable to fetch signal explanation."
        )
        return

    signal = explanation.get("signal") or analysis.get("signal") or "HOLD"
    confidence = explanation.get("confidence")
    confidence_text = (
        f"{confidence:.0f}%"
        if isinstance(confidence, (int, float))
        else "N/A"
    )

    emoji = {
        "BUY": "🟢",
        "HOLD": "🟡",
        "SELL": "🔴"
    }.get(signal, "⚪")

    reasons = explanation.get("reasons") or []
    risks = explanation.get("risks") or []

    message = (
        f"🧠 Signal Analysis: {explanation.get('symbol') or symbol.upper()}\n\n"
        f"{emoji} Signal: {signal} ({confidence_text} confidence)\n"
        f"📊 Score: {analysis.get('score', 'N/A')}\n\n"
        f"📝 {explanation.get('summary') or 'No summary available.'}\n"
    )

    if reasons:
        message += "\n🔍 Reasons:\n" + "\n".join(f"• {r}" for r in reasons)

    if risks:
        message += "\n\n⚠️ Risks:\n" + "\n".join(f"• {r}" for r in risks)

    message += (
        f"\n\n💡 Recommendation: {explanation.get('recommendation') or 'N/A'}"
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
    app.add_handler(CommandHandler("strongbuy", strongbuy))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("watchlist", watchlist))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("backtest", backtest))
    app.add_handler(CommandHandler("signals", signals))

    logger.info("🚀 NEPSE Quant Engine Telegram Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()