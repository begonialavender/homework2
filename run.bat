@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 goto :fail
)

call ".venv\Scripts\activate.bat"

python -c "import flask, pymongo, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo [2/3] Installing dependencies...
  python -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
)

echo [3/3] Initializing database...
python schema\init_db.py
if errorlevel 1 (
  echo.
  echo Database init failed. Make sure MongoDB is running on 127.0.0.1:27017.
  pause
  exit /b 1
)

echo.
echo Starting server: http://127.0.0.1:5000
echo Press Ctrl+C to stop.
python run.py
pause
exit /b 0

:fail
echo.
echo Setup failed. Check the messages above.
pause
exit /b 1
