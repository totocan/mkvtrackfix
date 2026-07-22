@echo off
cd /d "%~dp0"
if exist "python\python.exe" (
  python\python.exe tmdb_manager.py
  pause
) else (
  python tmdb_manager.py
  pause
)
