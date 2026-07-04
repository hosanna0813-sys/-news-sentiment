@echo off
REM News Sentiment Desktop V4.1.0 (Claude Edition) - Normal Startup
cd /d "%~dp0"

if not exist ".venv" (
    echo [First run] Creating virtual environment...
    python -m venv .venv 2>nul
    if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [Error] Python not found. Install Python 3.10+ or add it to PATH.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

echo Checking / installing required packages...
pip install -q -r requirements.txt

if not exist ".venv\pw_chromium_installed" (
    echo [First run] Installing Chromium for browser rendering fallback...
    echo (This downloads ~150MB once. Safe to skip with Ctrl+C; the app still works without it.)
    playwright install chromium && echo done > .venv\pw_chromium_installed
)

echo Starting application...
python run_desktop.py

pause
