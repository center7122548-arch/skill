@echo off
setlocal
cd /d "%~dp0"
echo This deletes ALL accounts and data, then creates the sample accounts again.
py -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 (
  py server.py --reset-accounts
) else (
  python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10 or newer is required. Install Python, then try again.
    pause
    exit /b 1
  )
  python server.py --reset-accounts
)
pause
