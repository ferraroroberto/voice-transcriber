@echo off
chcp 65001 >nul
REM ============================================================================
REM  QUICK RECORD — one-shot record / transcribe / copy / exit
REM ----------------------------------------------------------------------------
REM  Intended for Elgato Stream Deck. Blocks until you stop the recording
REM  (Enter key) or hit the max duration, then copies the text and exits.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    for %%I in ("%SCRIPT_DIR%..\..") do set "OUTER_DIR=%%~fI"
    set "VENV_PY=%OUTER_DIR%\.venv\Scripts\python.exe"
)

cd /d "%SCRIPT_DIR%" || exit /b 1

title Voice Transcription (press ENTER to stop)
mode con: cols=80 lines=20

if exist "%VENV_PY%" (
    "%VENV_PY%" launcher.py record %*
) else (
    python launcher.py record %*
)

set "RC=%ERRORLEVEL%"
exit /b %RC%
