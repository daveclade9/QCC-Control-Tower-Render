@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo Python could not create the virtual environment.
  echo Install Python 3.10 or newer, then run this setup again.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt

if not exist ".env" copy /Y ".env.example" ".env" >nul

echo.
echo Setup is complete.
echo Open .env and use the same Supabase values as Version 0.8.9.
pause
