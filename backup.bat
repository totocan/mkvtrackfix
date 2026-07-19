@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "python\python.exe" (
    echo Portable Python not found.
    echo Please run build_portable.bat first.
    pause
    exit /b 1
)

for /f "delims=" %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%t
set OUT=mediameta_fixer_backup_%TS%.zip

echo Creating personal backup: %OUT%
echo includes source + python + models + tools + your config; excludes logs
call python\python.exe -c "import sys; sys.path.insert(0, r'%CD%'); import mmf_pack; n=mmf_pack.backup(r'%CD%', r'%OUT%'); print('Packed', n, 'files')"
echo Done: %OUT%
echo Unzip this into a new folder to migrate without rebuilding the environment.
pause
