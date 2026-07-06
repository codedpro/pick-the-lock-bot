@echo off
REM Double-click this to (re)calibrate the bot for your screen.
cd /d "%~dp0"
call "%~dp0_setup.bat"
if not %errorlevel%==0 exit /b 1
"%~dp0.venv\Scripts\python.exe" "%~dp0pick_the_lock_bot.py" --calibrate
pause
