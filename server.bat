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
    echo [ERROR] .venv missing. Run setup.bat first.
    exit /b 1
)

cd /d "%SCRIPT_DIR%" || exit /b 1

"%VENV_PY%" launcher.py server %*
exit /b %ERRORLEVEL%
