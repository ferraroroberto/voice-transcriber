@echo off
chcp 65001 >nul
REM ============================================================================
REM  TRANSCRIBE VOICE — main window launcher
REM ----------------------------------------------------------------------------
REM  Launches the tkinter main window. The window shows server status and
REM  lets you start/stop the whisper-server.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    for %%I in ("%SCRIPT_DIR%..\..") do set "OUTER_DIR=%%~fI"
    set "VENV_PY=%OUTER_DIR%\.venv\Scripts\python.exe"
)

cd /d "%SCRIPT_DIR%" || (echo [ERROR] Could not cd to %SCRIPT_DIR% & exit /b 1)

if exist "%VENV_PY%" (
    "%VENV_PY%" launcher.py gui
) else (
    echo [INFO] .venv not found — falling back to system python
    python launcher.py gui
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Exited with code %RC%.
    pause >nul
)
exit /b %RC%
