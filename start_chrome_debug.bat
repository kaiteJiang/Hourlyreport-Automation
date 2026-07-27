@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv not found. Please run install_env.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m modules.chrome_debug_launcher
pause
