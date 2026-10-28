@echo off
setlocal
rem ------------------------------------------------------------------
rem  One-time setup (safe to run again to repair or update).
rem  Needs internet for: Python packages, FFmpeg (only if missing),
rem  and the speech model download.
rem  No administrator rights needed; nothing is installed system-wide.
rem ------------------------------------------------------------------
cd /d "%~dp0"
title Transcription setup

echo ==================================================
echo  Transcription app setup
echo  Folder: %CD%
echo ==================================================
echo.

rem --- 1. Find a supported Python (3.11 - 3.14) ----------------------
set "PYEXE="
set "PYCHECK=import sys; print(sys.executable) if (3,11) <= sys.version_info[:2] <= (3,14) else None"
for %%V in (3.12 3.13 3.14 3.11) do (
    if not defined PYEXE (
        for /f "usebackq delims=" %%P in (`py -%%V -c "%PYCHECK%" 2^>nul`) do set "PYEXE=%%P"
    )
)
if not defined PYEXE (
    for /f "usebackq delims=" %%P in (`python -c "%PYCHECK%" 2^>nul`) do set "PYEXE=%%P"
)
if not defined PYEXE (
    echo [ERROR] No supported Python found. This app needs Python 3.11, 3.12, 3.13 or 3.14.
    echo         ^(Newer versions such as 3.15 are not yet supported by the speech packages.^)
    echo.
    echo  How to fix:
    echo    1. Open https://www.python.org/downloads/windows/
    echo    2. Download the "Windows installer (64-bit)" for Python 3.12.
    echo    3. Run it. Keep "Use admin privileges" unticked if you have no admin rights,
    echo       and tick "Add python.exe to PATH".
    echo    4. Run setup.bat again.
    echo.
    echo  Or, in a terminal:  winget install Python.Python.3.12
    set "RC=10"
    goto :end
)
echo [OK] Python: %PYEXE%

rem --- 2. Project-local virtual environment ---------------------------
set "VPY=%CD%\.venv\Scripts\python.exe"
if exist "%VPY%" (
    "%VPY%" -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [..] Existing .venv is broken or uses an unsupported Python; recreating it.
        rmdir /s /q ".venv"
    ) else (
        echo [OK] Reusing existing virtual environment .venv
    )
)
if not exist "%VPY%" (
    echo [..] Creating virtual environment in .venv
    "%PYEXE%" -m venv ".venv"
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        echo         Make sure the folder is writable, then run setup.bat again.
        set "RC=11"
        goto :end
    )
)

rem --- 3. Python packages (internet) ---------------------------------
echo.
echo [..] Installing / updating Python packages (needs internet)...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install --upgrade --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed.
    echo         Check your internet connection ^(and proxy/firewall^), then run setup.bat again.
    echo         Details are shown above.
    set "RC=12"
    goto :end
)
echo [OK] Python packages installed.

rem --- 4. External tools, model download and final checks -------------
echo.
"%VPY%" setup_check.py %*
set "RC=%ERRORLEVEL%"

:end
echo.
if not "%RC%"=="0" echo Setup finished with errors ^(code %RC%^).
pause
endlocal & exit /b %RC%
