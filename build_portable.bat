@echo off
setlocal EnableExtensions
pushd "%~dp0"

REM ============================================================
REM  MediaMetaFixer portable package builder (Windows 10/11)
REM  Needs internet on first run (downloads Python + deps + model + tools).
REM  After that, just run run.bat - fully offline, no install needed.
REM ============================================================

set PYVER=3.11.9
set PYDIR=python
set PYEMBED_URL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip
set GETPIP_URL=https://bootstrap.pypa.io/get-pip.py

echo ============================================
echo   MediaMetaFixer portable builder
echo ============================================
echo.

REM ---------- 1) Portable Python ----------
if exist "%PYDIR%\python.exe" (
    echo [1/5] Portable Python already present, checking health ...
    REM health: embed stdlib lives in python311.zip; missing => encodings error
    if not exist "%PYDIR%\python311.zip" (
        echo    WARNING: python311.zip ^(stdlib^) missing, forcing rebuild.
        rmdir /s /q "%PYDIR%" 2>nul
        call :setup_python
    ) else (
        REM actually run import encodings; crash => Lib corrupt
        "%PYDIR%\python.exe" -c "import encodings,sys;print('python ok')" >nul 2>&1
        if errorlevel 1 (
            echo    WARNING: existing Python is broken, encodings import failed.
            echo    Forcing clean rebuild of portable Python ...
            rmdir /s /q "%PYDIR%" 2>nul
            call :setup_python
        ) else (
            echo    Python health check passed, skip.
        )
    )
) else (
    call :setup_python
)

REM ---------- 2) Python dependencies ----------
echo [2/5] Installing dependencies (faster-whisper, PyQt5, RapidOCR, ...)
call "%PYDIR%\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo    pip upgrade failed; please check network and re-run.
    pause
    exit /b 1
)
call "%PYDIR%\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo    pip install -r failed; retrying key packages individually ...
    "%PYDIR%\python.exe" -m pip install psutil requests langdetect PyQt5 faster-whisper rapidocr-openvino
    if errorlevel 1 (
        echo    Still failing. Check network or proxy, then re-run.
        pause
        exit /b 1
    )
    echo    Key packages installed.
) else (
    REM requirements.txt now includes rapidocr-onnxruntime, verify
    "%PYDIR%\python.exe" -c "from rapidocr_openvino import RapidOCR; print('RapidOCR OK')" >nul 2>&1
    if errorlevel 1 (
        echo    RapidOCR not found in requirements, installing manually ...
        "%PYDIR%\python.exe" -m pip install rapidocr-openvino
    )
)

REM ---------- 3) AI model (for offline use) ----------
echo [3/5] Downloading faster-whisper model into models, first time only...
if exist "models\medium\model.bin" (
    echo    medium model already present, skip.
) else (
    call "%PYDIR%\python.exe" download_model.py medium --source modelscope
)

REM ---------- 4) Native tools ----------
echo [4/5] Preparing native tools ffmpeg / mkvmerge / RapidOCR...
call :get_ffmpeg
call :get_mkvmerge
echo    RapidOCR installed via pip (rapidocr-openvino)

REM ---------- 5) Done ----------
echo [5/5] Creating run.bat ...
if exist run.bat goto :skip_run_bat

REM                                  CMD                         
echo @echo off> run.bat
echo pushd "%%~dp0">> run.bat
echo if not exist "python\python.exe" (>> run.bat
echo    echo Please run build_portable.bat first.>> run.bat
echo    pause ^& exit /b 1>> run.bat
echo )>> run.bat
echo python\python.exe -c "import encodings" ^>nul 2^>^&1 ^|^| (>> run.bat
echo    echo Portable Python is broken; please re-run build_portable.bat.>> run.bat
echo    pause ^& exit /b 1>> run.bat
echo )>> run.bat
echo call python\python.exe main.py>> run.bat
echo pause>> run.bat

:skip_run_bat

REM ------ Color helper (ANSI escape codes, Win10+) ------
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"

echo.
echo ============================================
echo   Build summary:
echo.
set ALL_OK=1

REM --- ffmpeg ---
if exist "tools\ffmpeg.exe" (
    echo %ESC%[92m  [OK]%ESC%[0m ffmpeg
) else (
    echo %ESC%[91m  [MISS]%ESC%[0m ffmpeg
    set ALL_OK=0
)

REM --- mkvmerge ---
if exist "tools\mkvmerge.exe" (
    echo %ESC%[92m  [OK]%ESC%[0m mkvmerge
) else (
    if exist "tools\MKVToolNix\mkvmerge.exe" (
        echo %ESC%[92m  [OK]%ESC%[0m mkvmerge
    ) else (
        echo %ESC%[91m  [MISS]%ESC%[0m mkvmerge
        set ALL_OK=0
    )
)

REM --- RapidOCR (verify via import) ---
"%PYDIR%\python.exe" -c "from rapidocr_openvino import RapidOCR; print('OK')" >nul 2>&1
if errorlevel 1 (
    echo %ESC%[91m  [MISS]%ESC%[0m RapidOCR - install failed or not installed
    echo    Run: %PYDIR%\python.exe -m pip install rapidocr-openvino
    set ALL_OK=0
) else (
    echo %ESC%[92m  [OK]%ESC%[0m RapidOCR
)

echo.
echo ============================================
if "%ALL_OK%"=="1" (
    echo %ESC%[92mAll tools ready!%ESC%[0m
    echo From now on just double-click run.bat, no internet needed.
) else (
    echo %ESC%[93mSome tools are MISSING.%ESC%[0m
    echo Fix the missing ones, then re-run this script.
)
echo.

REM ---- cleanup temp build files ----
if exist python-embed.zip del python-embed.zip
if exist get-pip.py del get-pip.py
if exist 7za920.zip del 7za920.zip
if exist mkv.7z del mkv.7z
if exist ffmpeg.zip del ffmpeg.zip
if exist tools\7za.exe del /q tools\7za.exe
if exist tools\7za920 rmdir /s /q tools\7za920
if exist tess.zip del tess.zip 2>nul
if exist se.zip del se.zip 2>nul
echo    cleaned up temp build files.
echo.
pause
exit /b


REM ===================== subroutines =====================

:setup_python
echo [1/5] Downloading and extracting portable Python %PYVER% ...
if exist "%PYDIR%" rmdir /s /q "%PYDIR%" 2>nul
curl.exe -L -o python-embed.zip "%PYEMBED_URL%" || (echo Download failed, check network & pause & exit /b 1)
mkdir "%PYDIR%" 2>nul
powershell -NoProfile -Command "Expand-Archive -Force python-embed.zip '%PYDIR%'"
del python-embed.zip
REM verify: embed stdlib must be python311.zip; missing => extract failed
if not exist "%PYDIR%\python311.zip" (
    echo    ERROR: python311.zip ^(stdlib^) missing after extract.
    echo    The embed zip may have failed to download/extract. Try manually:
    echo      step 1: delete python\ folder
    echo      step 2: re-run this script
    pause
    exit /b 1
)
REM enable import site (embed disables it by default, otherwise pip packages won't be found)
powershell -NoProfile -Command "(Get-Content '%PYDIR%\python311._pth') -replace '#import site','import site' | Set-Content '%PYDIR%\python311._pth'"
REM confirm Python can import encodings now
"%PYDIR%\python.exe" -c "import encodings;print('python ok')" >nul 2>&1
if errorlevel 1 (
    echo    ERROR: Python still broken after setup, encodings import failed.
    pause
    exit /b 1
)
echo    Installing pip ...
curl.exe -L -o get-pip.py "%GETPIP_URL%" || (echo get-pip download failed & pause & exit /b 1)
call "%PYDIR%\python.exe" get-pip.py
if errorlevel 1 (
    echo    get-pip install failed.
    pause
    exit /b 1
)
del get-pip.py
exit /b

:get_ffmpeg
if exist "tools\ffmpeg.exe" echo    ffmpeg already present.& exit /b
echo    Downloading ffmpeg portable zip ...
curl.exe -L -o ffmpeg.zip "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" 2>nul
if not exist ffmpeg.zip echo    ffmpeg download failed; put ffmpeg.exe into tools\ manually.& exit /b
mkdir tools 2>nul
call "%PYDIR%\python.exe" -c "import zipfile;zipfile.ZipFile('ffmpeg.zip').extractall('tools')"
if exist "tools\ffmpeg.exe" (del ffmpeg.zip) else (echo    Warning: ffmpeg.exe not found; keeping ffmpeg.zip)
for /r "tools" %%f in (ffmpeg.exe ffprobe.exe) do copy /Y "%%f" "tools\" >nul 2>&1
echo    ffmpeg ready.
exit /b

:get_mkvmerge
if exist "tools\mkvmerge.exe" echo    mkvmerge already present.& exit /b
if exist "tools\MKVToolNix\mkvmerge.exe" echo    mkvmerge already present.& exit /b
echo    Downloading MKVToolNix portable 7z ...
curl.exe -L -o mkv.7z "https://mkvtoolnix.download/windows/releases/100.0/mkvtoolnix-64-bit-100.0.7z"
if not exist mkv.7z echo    MKVToolNix download failed; put mkvmerge.exe into tools\ manually.& exit /b
REM --- extract 7z: prefer a temp standalone 7za.exe; fallback to py7zr ---
if not exist "tools\7za.exe" (
    echo    fetching 7-zip standalone 7za.exe for 7z extraction ...
    curl.exe -L -o 7za920.zip "https://7-zip.org/a/7za920.zip"
    if exist 7za920.zip (
        call "%PYDIR%\python.exe" -c "import zipfile;zipfile.ZipFile('7za920.zip').extractall('tools')"
        del 7za920.zip
        REM 7za920.zip extracts to tools\7za920\7za.exe, lift it to tools\
        if exist "tools\7za920\7za.exe" (
            copy /Y "tools\7za920\7za.exe" "tools\7za.exe" >nul 2>&1
            rmdir /s /q "tools\7za920" 2>nul
        )
    )
)
if exist "tools\7za.exe" (
    echo    Extracting MKVToolNix with 7za ...
    "tools\7za.exe" x -y -otools mkv.7z >nul
    del mkv.7z
) else (
    REM fallback: try py7zr if installed
    echo    Warning: 7za.exe unavailable, trying py7zr ...
    call "%PYDIR%\python.exe" -c "import py7zr;py7zr.SevenZipFile('mkv.7z','r').extractall(path='tools')" 2>nul
    del mkv.7z
)
REM normalize nested dir after extract
if exist "tools\MKVToolNix\mkvmerge.exe" (
    echo    mkvmerge ready at tools\MKVToolNix\mkvmerge.exe.
) else (
    for /d %%d in (tools\mkvtoolnix-*) do (
        if exist "%%d\mkvmerge.exe" (
            if not exist "tools\MKVToolNix" mkdir "tools\MKVToolNix"
            xcopy /E /Y "%%d\*" "tools\MKVToolNix\" >nul 2>&1
            rmdir /s /q "%%d" 2>nul
            echo    mkvmerge ready at tools\MKVToolNix\mkvmerge.exe.
        )
    )
    if not exist "tools\MKVToolNix\mkvmerge.exe" (
        echo    Warning: mkvmerge.exe not found after extract.
    )
)
exit /b
