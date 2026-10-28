@echo off
setlocal
rem Double-click to transcribe every link file in the input folder.
rem Extra arguments are passed through, e.g.  run.bat --language ur
cd /d "%~dp0"
title Transcription

if not exist ".venv\Scripts\python.exe" (
    echo The app is not set up yet.
    echo Double-click setup.bat in this folder first, then run this file again.
    set "RC=3"
    goto :end
)

".venv\Scripts\python.exe" main.py %*
set "RC=%ERRORLEVEL%"

:end
call :pause_if_double_clicked
endlocal & exit /b %RC%

:pause_if_double_clicked
rem When started from Explorer, cmd's command line contains this script's name.
setlocal EnableDelayedExpansion
set "CMDLINE=!cmdcmdline!"
set "STRIPPED=!CMDLINE:%~nx0=!"
if /i not "!STRIPPED!"=="!CMDLINE!" (
    echo.
    pause
)
endlocal
exit /b 0
