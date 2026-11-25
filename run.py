"""Set up (first time only) and start News Hunter: python run.py

Creates .venv, installs the dependencies whenever pyproject.toml changes,
creates .env from .env.example, then starts the web app and opens
http://127.0.0.1:8000/monitor. Uses only the standard library, so it works
with any Python 3.11+ before anything is installed.

Options: --port 8000, --host 127.0.0.1, --no-browser
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".news-hunter-installed"


def step(text: str) -> None:
    print(f"==> {text}", flush=True)


def run(*args: object) -> None:
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def venv_works() -> bool:
    # A .venv copied from another PC or folder still exists but points at a
    # Python that is not here, so test it instead of trusting that it exists.
    if not VENV_PYTHON.exists():
        return False
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import sys"], check=False, capture_output=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def ensure_venv() -> None:
    if venv_works():
        return
    if VENV.exists():
        step("The .venv folder came from another PC or folder - rebuilding it")
        shutil.rmtree(VENV)
    step("Creating the virtual environment (.venv)")
    run(sys.executable, "-m", "venv", VENV)


def ensure_dependencies() -> None:
    wanted = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text().strip() == wanted:
        return
    step("Installing dependencies (a few minutes the first time)")
    pip = (VENV_PYTHON, "-m", "pip", "install", "--disable-pip-version-check", "--quiet")
    run(*pip, "--upgrade", "pip")
    run(*pip, "-e", ".")
    STAMP.write_text(wanted)


def ensure_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copyfile(ROOT / ".env.example", env)
        step("Created .env - add GEMINI_API_KEY there to turn on AI sorting and summaries")


def open_when_ready(url: str) -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=2)
        except OSError:
            time.sleep(1)
            continue
        webbrowser.open(url)
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up and start News Hunter.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if sys.version_info < (3, 11):  # noqa: UP036 - runs on whatever python the user has
        print("News Hunter needs Python 3.11 or newer: https://www.python.org/downloads/")
        return 1
    try:
        ensure_venv()
        ensure_dependencies()
        ensure_env_file()
    except subprocess.CalledProcessError as exc:
        print(f"\nSetup failed ({exc}). Check the internet connection or proxy, then run again.")
        return 1

    url = f"http://{args.host}:{args.port}/monitor"
    step(f"Starting News Hunter at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    server = [VENV_PYTHON, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(args.port)]
    try:
        return subprocess.call([str(a) for a in server], cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
