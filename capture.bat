@echo off
REM Captures the lock dial so detection can be tuned.
REM Press F2 with the dial + a highlighted bar visible; Esc to finish.
cd /d "%~dp0"
call "%~dp0_setup.bat"
if not %errorlevel%==0 exit /b 1
"%~dp0.venv\Scripts\python.exe" "%~dp0_capture.py"
pause
