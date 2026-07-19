@echo off
cd /d "%~dp0"
if exist "python\pythonw.exe" (
  start "" "python\pythonw.exe" tray_monitor.py
) else if exist "python\python.exe" (
  start "" "python\python.exe" tray_monitor.py
) else (
  start "" pythonw tray_monitor.py
)
