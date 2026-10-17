@echo off
setlocal
cd /d "%~dp0"
echo This deletes ALL accounts and data, then creates the sample accounts again.
python server.py --reset-accounts
pause
