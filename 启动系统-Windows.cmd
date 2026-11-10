@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launcher.py
  goto :done
)
where py >nul 2>nul
if %errorlevel%==0 (py -3 launcher.py) else (python launcher.py)
:done
pause
