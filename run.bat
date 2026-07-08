@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv
)
.venv\Scripts\pip.exe install -r requirements.txt
.venv\Scripts\python.exe main.py %*
pause
