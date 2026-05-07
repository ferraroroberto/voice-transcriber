@echo off
chcp 65001 >nul
REM ============================================================================
REM  TRANSCRIBE VOICE - tray + global hotkey (day-to-day mode)
REM ----------------------------------------------------------------------------
REM  Runs resident in the system tray. Default hotkey: Ctrl+Alt+Space.
REM  Launch this on login (Startup folder) for always-on voice input.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
REM Prefer a repo-local .venv (standalone layout), fall back to the enclosing
REM automation repo's .venv for the in-place case.
set "VENV_PYW=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PYW%" (
    for %%I in ("%SCRIPT_DIR%..\..") do set "OUTER_DIR=%%~fI"
    set "VENV_PYW=%OUTER_DIR%\.venv\Scripts\pythonw.exe"
    set "VENV_PY=%OUTER_DIR%\.venv\Scripts\python.exe"
)

cd /d "%SCRIPT_DIR%" || exit /b 1

REM Prefer pythonw.exe so no console window stays open.
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" launcher.py tray
) else if exist "%VENV_PY%" (
    start "" "%VENV_PY%" launcher.py tray
) else (
    start "" pythonw launcher.py tray
)
exit /b 0
