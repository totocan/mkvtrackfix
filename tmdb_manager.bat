@echo off
cd /d "%~dp0"
if exist "python\pythonw.exe" (
  start "" "python\pythonw.exe" tmdb_manager.py
) else if exist "python\python.exe" (
  start "" "python\python.exe" tmdb_manager.py
) else (
  start "" pythonw tmdb_manager.py
)
