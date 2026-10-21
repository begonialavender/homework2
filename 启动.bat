@echo off
title Survey System Launcher
cd /d "%~dp0"

echo ============================================
echo    Survey System - one click launcher
echo ============================================
echo.

rem ---- 0. already running? ----
netstat -ano | findstr /c:":5000 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [0/3] Web app is already running on port 5000.
    echo       Opening browser ...
    start "" http://127.0.0.1:5000
    timeout /t 2 /nobreak >nul
    exit /b 0
)

rem ---- 1. MongoDB ----
netstat -ano | findstr /c:":27017 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [1/3] MongoDB already listening on 27017 - skip.
    goto flask
)

echo [1/3] Looking for mongod.exe ...
set "MONGOD="
for /f "delims=" %%P in ('powershell -NoProfile -Command "Get-ChildItem -Path 'D:\' -Filter mongod.exe -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName" 2^>nul') do set "MONGOD=%%P"
if not defined MONGOD (
    where mongod.exe >nul 2>&1
    if not errorlevel 1 for /f "delims=" %%P in ('where mongod.exe') do set "MONGOD=%%P"
)
if not defined MONGOD (
    echo       [ERROR] mongod.exe not found.
    echo       Edit this file and set MONGOD to the full path.
    echo.
    pause
    exit /b 1
)
echo       found: %MONGOD%

for %%I in ("%MONGOD%") do set "MONGO_BIN=%%~dpI"
for %%I in ("%MONGO_BIN%..") do set "MONGO_HOME=%%~fI"
set "DBPATH=%MONGO_HOME%\data2"
if not exist "%DBPATH%" mkdir "%DBPATH%"

echo       starting mongod, dbpath: %DBPATH%
start "MongoDB" /min "%MONGOD%" --dbpath "%DBPATH%"

echo [2/3] Waiting for port 27017 ...
set /a tries=0
:wait
set /a tries+=1
timeout /t 1 /nobreak >nul
netstat -ano | findstr /c:":27017 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto ok
if %tries% GEQ 45 (
    echo       [ERROR] MongoDB did not open port 27017 in 45s.
    echo       Open the minimized "MongoDB" window to see the reason.
    echo.
    pause
    exit /b 1
)
goto wait

:ok
echo       MongoDB is up.

:flask
echo [3/3] Starting web app ...
start /min cmd /c "timeout /t 8 /nobreak >nul & start http://127.0.0.1:5000"
call "%~dp0run.bat"

echo.
echo ============================================
echo    Server stopped. Press any key to close.
echo ============================================
pause >nul
