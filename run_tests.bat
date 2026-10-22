@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" call ".venv\Scripts\activate.bat"
echo Running tests against MongoDB on 127.0.0.1:27017 ...
python -m pytest -q
echo.
pause
