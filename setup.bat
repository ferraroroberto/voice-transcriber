@echo off
chcp 65001 >nul
REM ============================================================================
REM  SETUP — one-shot installer for a fresh clone
REM ----------------------------------------------------------------------------
REM  1. Creates .venv (if missing).
REM  2. Installs Python deps from requirements.txt.
REM  3. Downloads the prebuilt whisper.cpp release (cuBLAS build on Windows).
REM  4. Fetches ggml-large-v3-turbo.bin from HuggingFace.
REM  After this runs once, `tray.bat` is enough for day-to-day use.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || exit /b 1

set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [1/4] Creating .venv...
    python -m venv .venv || exit /b 1
)

echo [2/4] Installing Python requirements...
"%VENV_PY%" -m pip install --upgrade pip || exit /b 1
"%VENV_PY%" -m pip install -r requirements.txt || exit /b 1

echo [3/4] Installing prebuilt whisper.cpp...
"%VENV_PY%" scripts\install_whisper_cpp.py || exit /b 1

echo [4/4] Downloading ggml model...
"%VENV_PY%" scripts\download_model.py || exit /b 1

echo.
echo ============================================================================
echo  Setup complete. Start the tray with:  tray.bat
echo ============================================================================
exit /b 0
