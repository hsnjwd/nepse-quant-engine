import time

from src.logging.logger import logger

from launcher.api_launcher import (
    start_api,
    stop_api,
    api_running,
)

from launcher.bot_launcher import (
    start_bot,
    stop_bot,
)

from launcher.dashboard import (
    start_dashboard,
    stop_dashboard,
    dashboard_running,
)


class ProcessManager:
    """
    Controls all background services of the
    NEPSE Quant Engine.
    """

    def __init__(self):

        self.api_process = None
        self.bot_process = None
        self.dashboard_process = None

    # ---------------------------------
    # API
    # ---------------------------------

    def start_api(self):

        if api_running():
            logger.info("✅ API already running.")
            return True

        self.api_process = start_api()

        logger.info("Waiting for API...")

        timeout = 30

        while timeout > 0:

            if api_running():

                logger.info("✅ API Online")

                return True

            time.sleep(1)

            timeout -= 1

        logger.error("❌ API failed to start.")

        return False

    # ---------------------------------
    # BOT
    # ---------------------------------

    def start_bot(self):

        self.bot_process = start_bot()

        logger.info("✅ Telegram Bot Started")

        return True

    # ---------------------------------
    # DASHBOARD
    # ---------------------------------

    def start_dashboard(self):

        if dashboard_running():
            logger.info("✅ Dashboard already running.")
            return True

        self.dashboard_process = start_dashboard()

        logger.info("✅ Streamlit Dashboard Started")

        return True

    # ---------------------------------
    # START EVERYTHING
    # ---------------------------------

    def start_all(self):

        logger.info("\nStarting NEPSE Quant Engine...\n")

        if not self.start_api():

            return False

        self.start_bot()

        self.start_dashboard()

        logger.info("\n✅ All services started successfully.\n")

        return True

    # ---------------------------------
    # STOP EVERYTHING
    # ---------------------------------

    def stop_all(self):

        logger.info("\nStopping services...\n")

        stop_bot(self.bot_process)

        stop_api(self.api_process)

        stop_dashboard(self.dashboard_process)

        logger.info("✅ All services stopped.")

    # ---------------------------------
    # RESTART
    # ---------------------------------

    def restart_all(self):

        logger.info("\nRestarting...\n")

        self.stop_all()

        time.sleep(2)

        self.start_all()

    # ---------------------------------
    # STATUS
    # ---------------------------------

    def status(self):

        return {
            "api": api_running(),
            "bot": self.bot_process is not None,
            "dashboard": dashboard_running(),
        }