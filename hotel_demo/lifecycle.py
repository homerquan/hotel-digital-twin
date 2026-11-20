"""Supervise one hotel service across dashboard-requested simulation resets."""

import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile
from threading import Event
from uuid import uuid4

STATE_DIR = Path(os.environ.get("HOTEL_STATE_DIR", Path(__file__).resolve().parents[1] / ".hotel-state"))
RUN_ENV = "HOTEL_RUN_ID"
RUN_ID = os.environ.get(RUN_ENV)
RUN_FILE = STATE_DIR / "run-id"


def current_run():
    try:
        return RUN_FILE.read_text().strip()
    except FileNotFoundError:
        return "initial"


def request_reset():
    run_id = uuid4().hex
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=STATE_DIR, delete=False) as stream:
        pending = Path(stream.name)
        stream.write(run_id)
    try:
        pending.chmod(0o644)
        pending.replace(RUN_FILE)
    finally:
        pending.unlink(missing_ok=True)
    return run_id


def stop_service(child):
    if child.poll() is not None:
        return
    child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def supervise(command):
    stopping = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    while not stopping.is_set():
        run_id = current_run()
        child = subprocess.Popen(command, env={**os.environ, RUN_ENV: run_id}, start_new_session=True)
        try:
            while child.poll() is None and not stopping.wait(.2):
                if current_run() != run_id:
                    # Give the dashboard a moment to return the reset confirmation.
                    stopping.wait(.4)
                    break
            if child.poll() is not None and current_run() == run_id and not stopping.is_set():
                return child.returncode
        finally:
            stop_service(child)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("A hotel service command is required.")
    if args[0] == "--python":
        args[0] = sys.executable
    sys.exit(supervise(args))
