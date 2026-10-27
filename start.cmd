@echo off
rem Start News Hunter on this PC and open it in the browser. Close this window to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo News Hunter is not set up on this PC yet. Run setup.cmd first.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8000/monitor"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
