@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================================
echo  tmdb cache diagnose
echo ============================================================
echo.

where python >nul 2>&1
if %errorlevel%==0 (
    set PY=python
) else (
    where py >nul 2>&1
    if %errorlevel%==0 (
        set PY=py
    ) else (
        echo ERROR: python not found. Install Python 3.11 and add to PATH.
        pause
        exit /b 1
    )
)
echo Python: %PY%
%PY% --version
echo.

set DB=
if exist "cache\tmdb_cache.db" set DB=%~dp0cache\tmdb_cache.db
if "%DB%"=="" if exist "%USERPROFILE%\Documents\tmdb_agent\cache\tmdb_cache.db" set DB=%USERPROFILE%\Documents\tmdb_agent\cache\tmdb_cache.db
if "%DB%"=="" if exist "%USERPROFILE%\Documents\tmdb\cache\tmdb_cache.db" set DB=%USERPROFILE%\Documents\tmdb\cache\tmdb_cache.db

echo DB: %DB%
if "%DB%"=="" (
    echo ERROR: tmdb_cache.db not found
    pause
    exit /b 1
)
echo.

echo Running diagnose... (old db may take minutes to build index)
echo.
%PY% "%~dp0diagnose_tmdb.py" "%DB%" > "%~dp0diag.txt" 2>&1
echo ============================================================
echo Done. Result saved to: %~dp0diag.txt
echo ============================================================
echo.
echo --- preview first 80 lines ---
type "%~dp0diag.txt"
echo.
echo --- open diag.txt for full content ---
pause
