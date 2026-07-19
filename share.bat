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
set OUT=mediameta_fixer_portable_%TS%.zip

echo Creating shareable offline package: %OUT%
echo includes source + python + models + tools; excludes your config and logs
call python\python.exe -c "import sys; sys.path.insert(0, r'%CD%'); import mmf_pack; n=mmf_pack.share(r'%CD%', r'%OUT%'); print('Packed', n, 'files')"
echo Done: %OUT%
echo Recipients just unzip and run run.bat - no internet or install needed.
echo Tip: if Windows Explorer complains about long paths, extract with 7-Zip.
pause
