@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\reflex.exe" (
  echo Reflex is not installed in this folder.
  echo Run Setup QCC Reflex Pilot.bat first.
  pause
  exit /b 1
)

call ".venv\Scripts\reflex.exe" run --env prod --single-port
