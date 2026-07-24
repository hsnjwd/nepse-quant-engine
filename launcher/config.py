from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOGS = ROOT / "logs"
PIDS = ROOT / "pids"

API_PORT = 8000

REFRESH_SECONDS = 5

LOGS.mkdir(exist_ok=True)
PIDS.mkdir(exist_ok=True)