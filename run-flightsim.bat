@echo off
REM Start AeroSim Lab. Double-click this file, or run it from a prompt.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 play.py %*
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python play.py %*
    goto done
)

echo Python 3.10+ is not installed, or is not on PATH.
echo Get it from https://www.python.org/downloads/
echo When installing, tick "Add Python to PATH".

:done
REM Keep the window open when double-clicked so any message can be read.
if "%~1"=="" pause
