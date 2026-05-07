@echo off
chcp 65001 >nul
REM ============================================================================
REM  WEBAPP + CLOUDFLARE TUNNEL
REM ----------------------------------------------------------------------------
REM  Starts the webapp on :8443 (HTTPS if cert exists) and a Cloudflare quick
REM  tunnel that publishes a public https://*.trycloudflare.com URL.
REM
REM  The captured URL is also written to webapp/last_tunnel_url.txt so the
REM  tray (or a separate launcher) can surface it without screen-scraping
REM  the cloudflared console.
REM
REM  Press Ctrl+C to stop both. The url file is cleared on exit.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [ERROR] .venv missing. Run setup.bat first.
    exit /b 1
)

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cloudflared not installed.
    echo   winget install Cloudflare.cloudflared
    pause
    exit /b 1
)

cd /d "%SCRIPT_DIR%" || exit /b 1

"%VENV_PY%" scripts\run_tunnel.py
exit /b %ERRORLEVEL%
