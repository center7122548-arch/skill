@echo off
setlocal
cd /d "%~dp0"
echo This deletes ALL accounts and data, then creates the sample accounts again.
py --version >nul 2>nul
if not errorlevel 1 (
  py server.py --reset-accounts
) else (
  python server.py --reset-accounts
)
pause
