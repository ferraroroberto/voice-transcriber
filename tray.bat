@echo off
chcp 65001 >nul
REM ============================================================================
REM  TRANSCRIBE VOICE - tray + global hotkey (day-to-day mode)
REM ----------------------------------------------------------------------------
REM  Runs resident in the system tray. Default hotkey: F8.
REM  Launch this on login (Startup folder) for always-on voice input.
REM
REM  Idempotent:
REM    tray.bat              -> no-op if a VoiceTranscriber tray is already running
REM    tray.bat --restart    -> stop the running tray (and its tree: webapp on
REM                             :8443, cloudflared) and start a fresh one
REM
REM  Detection matches the tray process by command line + this project's .venv
REM  path via CIM, then kills BY PID with /T. We never blanket-kill pythonw,
REM  so sister-app trays (AppLauncher, PhotoOCR, local-llm-hub, ...) and
REM  any other unrelated python processes are untouched.
REM
REM  --restart is orphan-proof: in addition to killing the tray subtree, it
REM  reclaims this app's webapp port :8443 by PID, regardless of process
REM  parentage. A webapp that got detached from its tray (stale process from
REM  an earlier run) would otherwise survive a subtree kill, block the fresh
REM  tray from binding, and keep serving the old build. The reclaim is scoped
REM  by CommandLine (not the process image path): a venv-launched pythonw
REM  re-execs the base interpreter, so the running webapp's image path reports
REM  the shared base python while CommandLine still carries the .venv path.
REM  See project-scaffolding#29.
REM
REM  IMPORTANT: ports :8090 and :8091 are SHARED with the sibling
REM  claude-local-calls hub (whisper-server and translate-server). They are
REM  deliberately NOT reclaimed here to avoid killing a running hub. Only the
REM  webapp port :8443, which this tray definitively owns, is reclaimed.
REM ============================================================================

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv\Scripts"
set "VENV_PYW=%VENV_DIR%\pythonw.exe"
set "VENV_PY=%VENV_DIR%\python.exe"

cd /d "%SCRIPT_DIR%" || exit /b 1

set "WANT_RESTART="
if /i "%~1"=="--restart" set "WANT_RESTART=1"
if /i "%~1"=="-r"        set "WANT_RESTART=1"

set "PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
set "TRAY_VENV=%SCRIPT_DIR%.venv"
set "TRAY_PIDS="
for /f "usebackq delims=" %%P in (`%PS% -NoProfile -NonInteractive -Command "$v=$env:TRAY_VENV; Get-CimInstance Win32_Process -Filter 'Name = ''pythonw.exe'' OR Name = ''python.exe''' | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($v, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.CommandLine -match 'launcher\.py\s+tray' } | Select-Object -ExpandProperty ProcessId"`) do (
    if defined TRAY_PIDS (set "TRAY_PIDS=!TRAY_PIDS! %%P") else (set "TRAY_PIDS=%%P")
)

if defined TRAY_PIDS if not defined WANT_RESTART (
    echo VoiceTranscriber tray is already running ^(PID: !TRAY_PIDS!^).
    echo Run "tray.bat --restart" to stop it and start fresh.
    exit /b 0
)

if defined WANT_RESTART (
    if defined TRAY_PIDS (
        echo Stopping previous VoiceTranscriber tray ^(PID: !TRAY_PIDS!^)...
        for %%P in (!TRAY_PIDS!) do (
            taskkill /T /F /PID %%P >nul 2>&1
        )
    )
    REM Orphan-proof: reclaim this app's webapp port :8443 from ANY holder whose
    REM command line is under this repo's .venv, even one detached from the tray
    REM subtree above. We match on CommandLine (not the process image path):
    REM a venv-launched pythonw re-execs the base interpreter, so .Path reports
    REM the shared base python while CommandLine still carries the .venv path.
    REM Matching the image path would miss the real webapp; the CommandLine scope
    REM keeps the sweep on THIS repo's children only.
    REM NOTE: :8090 and :8091 are intentionally excluded - they are mutex-shared
    REM with claude-local-calls (whisper-server, translate-server). Reclaiming
    REM them would kill a running sibling hub.
    set "RECLAIM_VENV=%SCRIPT_DIR%.venv"
    %PS% -NoProfile -NonInteractive -Command "$v=$env:RECLAIM_VENV; foreach ($port in 8443) { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $opid = $_.OwningProcess; $cim = Get-CimInstance Win32_Process -Filter ('ProcessId = {0}' -f $opid) -ErrorAction SilentlyContinue; if ($cim -and $cim.CommandLine -and $cim.CommandLine.IndexOf($v, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { Write-Host ('Reclaiming :{0} from PID {1}' -f $port, $opid); Stop-Process -Id $opid -Force -ErrorAction SilentlyContinue } } }"
    REM Give Windows a moment to release :8443 before rebinding.
    ping 127.0.0.1 -n 3 >nul
)

REM Prefer pythonw.exe so no console window stays open. The window title
REM differentiates this tray from sister apps' trays.
if exist "%VENV_PYW%" (
    start "VoiceTranscriber Tray" "%VENV_PYW%" launcher.py tray
) else if exist "%VENV_PY%" (
    start "VoiceTranscriber Tray" "%VENV_PY%" launcher.py tray
) else (
    start "VoiceTranscriber Tray" pythonw launcher.py tray
)
exit /b 0
