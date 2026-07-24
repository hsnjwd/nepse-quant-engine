import socket
import subprocess
import sys
from pathlib import Path

from src.logging.logger import logger

HOST = "127.0.0.1"
PORT = 8000

PID_FILE = Path("pids/api.pid")


def api_running():
    """
    Returns True if the FastAPI server is running.
    """
    sock = socket.socket()
    sock.settimeout(1)

    try:
        sock.connect((HOST, PORT))
        return True
    except Exception:
        logger.exception("API health check failed")
        return False
    finally:
        sock.close()


def start_api():
    """
    Starts the FastAPI server in the background.
    Returns the subprocess object.
    """

    if api_running():
        logger.info("✅ API already running.")
        return None

    logger.info("🚀 Starting API...")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--reload",
        ]
    )

    PID_FILE.parent.mkdir(exist_ok=True)

    PID_FILE.write_text(str(process.pid))

    return process


def stop_api(process=None):
    """
    Stops the API server.
    """

    if process is not None:
        process.terminate()
        process.wait()

    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)