@echo off
REM Diagnostic recorder — runs ~60s, logs what the bot detects and saves
REM annotated frames, but sends NO clicks. Press F8 to "activate" and play.
REM Afterwards, share the "diag" folder (diag_log.txt + frame_*.png).
cd /d "%~dp0"
call "%~dp0_setup.bat"
if not %errorlevel%==0 exit /b 1
"%~dp0.venv\Scripts\python.exe" "%~dp0pick_the_lock_bot.py" --diag
pause
