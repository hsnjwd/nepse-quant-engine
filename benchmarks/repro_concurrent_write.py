"""Repro for the Sprint 11.6 two-worker concurrent-write 500s.

Spawns ``uvicorn --workers=2`` with isolated state and fires concurrent
watchlist add/remove requests while capturing uvicorn stderr to a log,
so the underlying exception behind the 500s can be identified.

Usage::

    python -m benchmarks.repro_concurrent_write [--port 8895]
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks import corpus as corpus_mod  # noqa: E402


def _request(port: int, path: str, method: str = "GET") -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_ready(port: int) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise TimeoutError("API not ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="nepse_repro_"))
    data_dir = tmp / "data"
    state_root = tmp / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    spec = corpus_mod.select_corpus(10, seed=42)
    corpus_mod.copy_corpus(spec, data_dir)
    log_path = tmp / "uvicorn.log"

    env = dict(os.environ)
    env["DATA_DIRECTORY"] = str(data_dir)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.api.main:app",
             "--host", "127.0.0.1", "--port", str(args.port), "--workers", "2"],
            cwd=str(state_root), env=env, creationflags=creationflags,
            stdout=logf, stderr=logf,
        )
    try:
        _wait_ready(args.port)
        for rnd in range(args.rounds):
            barrier = threading.Barrier(8, timeout=30)
            results: list[tuple[int, int]] = []

            def writer(i: int) -> None:
                tag = f"WRK{rnd}{i:02d}"
                barrier.wait()
                a = _request(args.port, f"/watchlist/add/{tag}", "POST")
                b = _request(args.port, f"/watchlist/remove/{tag}", "DELETE")
                results.append((a, b))

            ts = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=60)
            non200 = [r for r in results if r != (200, 200)]
            print(f"round {rnd}: {len(results)}/8 completed, "
                  f"non-200s: {non200}")
        print(f"\n--- uvicorn stderr tail ({log_path}) ---")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if "Error" in ln or "error" in ln
                 or "Traceback" in ln or "PermissionError" in ln]
        print("\n".join(lines[-40:]) if lines else "(no error lines captured)")
        print(f"\n--- last 25 lines raw ---")
        print("\n".join(text.splitlines()[-25:]))
    finally:
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
