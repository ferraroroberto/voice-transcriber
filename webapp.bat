@echo off
chcp 65001 >nul
REM ============================================================================
REM  WEBAPP - standalone FastAPI launcher (HTTPS on :8443)
REM ----------------------------------------------------------------------------
REM  Daily use: launch tray.bat instead - it adopt-or-spawns the webapp for
REM  you. This bat is for headless boxes, dev iteration, or when you want
REM  the webapp without the tray icon and global hotkey.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] .venv missing. Run setup.bat first.
    exit /b 1
)

cd /d "%SCRIPT_DIR%" || exit /b 1

REM Argv construction (host/port/certs/uvicorn flags) lives in one place --
REM app.webapp.manager.build_uvicorn_command, via scripts\run_webapp.py --
REM so this bat can't drift from the tray's spawn path (voice-transcriber#174).
"%VENV_PY%" scripts\run_webapp.py

exit /b %ERRORLEVEL%
