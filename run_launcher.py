import time

from src.logging.logger import logger
from launcher.ascii import BANNER
from launcher.dashboard import show
from launcher.process_manager import ProcessManager


def main():

    logger.info(BANNER)

    manager = ProcessManager()

    if not manager.start_all():
        logger.error("\nFailed to start NEPSE Quant Engine.")
        return

    try:

        while True:

            show(manager.status())

            time.sleep(3)

    except KeyboardInterrupt:

        logger.exception("Keyboard interrupt received")

        manager.stop_all()

        logger.info("NEPSE Quant Engine stopped.")


if __name__ == "__main__":
    main()