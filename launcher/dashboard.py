import os
import time

from src.logging.logger import logger


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def show(status):

    clear()

    logger.info("=" * 60)
    logger.info("              NEPSE QUANT ENGINE")
    logger.info("=" * 60)
    logger.info("")
    logger.info("         Professional Trading Platform")
    logger.info("")
    logger.info("           NEPSE Quant Engine v1.0")
    logger.info("           Built by Jawed Hassan")
    logger.info("                 © 2026")
    logger.info("")
    logger.info("=" * 60)
    logger.info("")

    logger.info(
        f"API Server     {'🟢 Running' if status['api'] else '🔴 Stopped'}"
    )

    logger.info(
        f"Telegram Bot   {'🟢 Running' if status['bot'] else '🔴 Stopped'}"
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Press Ctrl+C to Exit")
    logger.info("")

    logger.info(
        f"Last Refresh: {time.strftime('%H:%M:%S')}"
    )