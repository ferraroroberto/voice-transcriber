@echo off
chcp 65001 >nul
REM ============================================================================
REM  WHISPER SERVER - start / stop / status / logs
REM ----------------------------------------------------------------------------
REM  Usage:
REM    server.bat start
REM    server.bat stop
REM    server.bat status
REM    server.bat logs
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    for %%I in ("%SCRIPT_DIR%..\..") do set "OUTER_DIR=%%~fI"
    set "VENV_PY=%OUTER_DIR%\.venv\Scripts\python.exe"
)

cd /d "%SCRIPT_DIR%" || exit /b 1

if exist "%VENV_PY%" (
    "%VENV_PY%" launcher.py server %*
) else (
    python launcher.py server %*
)
exit /b %ERRORLEVEL%
