@echo off
chcp 65001>NUL
cd /d "%~dp0"
python tmdb_manager.py
pause
