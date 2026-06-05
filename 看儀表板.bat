@echo off
chcp 65001 >nul
cd /d "%~dp0"
python view.py
if errorlevel 1 (
  echo.
  echo [!] python view.py failed - opening dashboard URL directly...
  start "" http://localhost:8765/congress_dashboard.html
  pause
)
