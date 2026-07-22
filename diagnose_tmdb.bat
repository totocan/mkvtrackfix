@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================================
echo  tmdb 缓存诊断 (双击运行,结果输出到 diag.txt)
echo ============================================================
echo.

REM 找 python
where python >nul 2>&1
if %errorlevel%==0 (
    set PY=python
) else (
    where py >nul 2>&1
    if %errorlevel%==0 (
        set PY=py
    ) else (
        echo !!! 没找到 python,请先安装 Python 并加入 PATH !!!
        pause
        exit /b 1
    )
)
echo 使用 Python: %PY%
%PY% --version
echo.

REM 找 db 路径
set DB=
if exist "cache\tmdb_cache.db" set DB=%~dp0cache\tmdb_cache.db
if "%DB%"=="" if exist "%USERPROFILE%\Documents\tmdb_agent\cache\tmdb_cache.db" set DB=%USERPROFILE%\Documents\tmdb_agent\cache\tmdb_cache.db
if "%DB%"=="" if exist "%USERPROFILE%\Documents\tmdb\cache\tmdb_cache.db" set DB=%USERPROFILE%\Documents\tmdb\cache\tmdb_cache.db

echo db 路径: %DB%
if "%DB%"=="" (
    echo !!! 找不到 db 文件 !!!
    echo 请把此 bat 放到 tmdb_manager.py 同目录,或先准备好 db
    pause
    exit /b 1
)
echo.

REM 跑诊断,所有输出重定向
echo 正在诊断,可能需要几秒到几分钟(老库会建索引)...
echo.
%PY% "%~dp0diagnose_tmdb.py" "%DB%" > "%~dp0diag.txt" 2>&1
echo ============================================================
echo  诊断完成,结果已保存到: %~dp0diag.txt
echo ============================================================
echo.
echo --- 预览前 80 行 ---
type "%~dp0diag.txt"
echo.
echo --- 完整内容请打开文件查看 ---
pause
