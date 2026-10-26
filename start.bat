@echo off
setlocal
cd /d "%~dp0"
py --version >nul 2>nul
if not errorlevel 1 (
  py server.py
  pause
  exit /b 0
)
python --version >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install Python, then try again.
  pause
  exit /b 1
)
python server.py
pause

