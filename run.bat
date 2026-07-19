@echo off
REM ============================================================
REM  MediaMetaFixer launcher - uses the bundled portable Python.
REM  No install needed on the host machine.
REM ============================================================
cd /d "%~dp0"

if not exist "python\python.exe" (
    echo Portable Python not found. Please run build_portable.bat first.
    pause
    exit /b 1
)

call python\python.exe main.py
pause 