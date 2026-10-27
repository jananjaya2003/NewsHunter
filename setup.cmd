@echo off
rem One-time setup on a new Windows PC. Options: -Dev (test tools), -Test (run tests).
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\setup.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the message above, fix it, then run setup.cmd again.
  pause
  exit /b 1
)
