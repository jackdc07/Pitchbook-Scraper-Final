@echo off
REM Double-click launcher for Windows.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py run.py
) else (
    python run.py
)
if %errorlevel% neq 0 (
    echo.
    echo If you saw "python is not recognized", install Python from
    echo https://www.python.org/downloads/ and tick "Add Python to PATH".
    pause
)
