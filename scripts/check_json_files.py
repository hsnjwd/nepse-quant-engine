import json
from pathlib import Path

SKIP = {".venv", ".git", ".freebuff", "__pycache__"}

for path in Path(".").rglob("*.json"):
    if any(part in SKIP for part in path.parts):
        continue

    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"INVALID JSON: {path}")
        print(f"ERROR: {exc}")
        print()
