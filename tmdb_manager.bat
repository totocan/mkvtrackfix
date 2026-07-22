@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo TMDB 缓存管理器启动中...
python tmdb_manager.py
pause
