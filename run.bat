@echo off
REM Quick start script for RAG Flask app (Windows)

setlocal enabledelayedexpansion

echo 🚀 Starting RAG Assistant...
echo.

REM Check if virtual environment exists
if not exist "venv" (
    echo 📦 Creating virtual environment...
    python -m venv venv
    echo.
)

REM Activate venv
echo 🔌 Activating virtual environment...
call venv\Scripts\activate.bat

REM Install/upgrade dependencies
echo 📥 Installing dependencies...
pip install -q -r requirements.txt

REM Create data directories if they don't exist
if not exist "data\raw\uploads" mkdir data\raw\uploads
if not exist "data\chroma" mkdir data\chroma

echo.
echo ✅ Everything ready!
echo.
echo 🌐 Starting Flask server on http://localhost:5000
echo    Press Ctrl+C to stop
echo.

REM Start the app
python app.py

pause
