# Pre-ship verification gate (issue #17).
#
# Runs the full validation pipeline locally before a change is declared
# "done": byte-compile, the static ratchet (no new duplicate block, unused
# import/variable, ruff B or S finding beyond .static-ratchet-baseline.json; exit 3 = a detector could
# not run = NOT CONFIRMED, which fails), the docs ratchet (README within 40 KiB and no
# new history marker in README/docs beyond .docs-ratchet-baseline.json; same exit-3
# rule), the structure ratchet (no file, function or directory past its limit, no
# src/ -> app/ import and no root folder outside the declared shape, beyond
# .structure-ratchet-baseline.json; exit 3 fails too), the conventions ratchet (no
# rule's hit count -- no-window, logging-not-print, no-drive-path -- above
# .conventions-ratchet-baseline.json; exit 3 fails too), the non-e2e pytest suite, then
# the Playwright e2e suite (Chromium + WebKit/iPhone projections) against a
# disposable webapp the script boots itself on a free port. The e2e stage is routed by the diff
# (scripts/e2e_route.py over the .fleet.toml [e2e] rules): nothing for a
# docs/backend-only diff, a smoke target or one surface's slice for a narrow one,
# and the whole suite for anything the router cannot read.
#
# Usage:
#   C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1
#   (never bare pwsh -- it's a 0-byte WindowsApps reparse stub on this machine that fails non-interactively)
#
# A tray on :8443 may be running or not — autoboot picks a free port for
# its own disposable webapp and tears it down on exit, so the running tray
# is never touched. Exits non-zero on the first failure with the offending
# output left visible.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$sw = [System.Diagnostics.Stopwatch]::StartNew()

function Fail($message) {
    Write-Host ""
    Write-Host "[X] $message" -ForegroundColor Red
    Write-Host ("Failed after {0:n1}s." -f $sw.Elapsed.TotalSeconds) -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $python)) {
    Fail ".venv missing -- run setup.bat first."
}

Push-Location $repoRoot
try {
    Write-Host "==> py_compile (app, src, tests)..." -ForegroundColor Cyan
    & $python -m compileall -q app src tests
    if ($LASTEXITCODE -ne 0) { Fail "byte-compile failed." }

    # Call the wrapper, never scripts/static_ratchet.py: the wrapper maps exit 3 to a
    # failure (tests/test_static_ratchet_gate.py pins it). Whole tree, never routed.
    Write-Host "==> static ratchet (duplicates, unused imports/variables, ruff B/S findings)..." -ForegroundColor Cyan
    & $python -m scripts.static_ratchet_gate
    if ($LASTEXITCODE -ne 0) { Fail "static ratchet failed (new finding, or a detector could not run)." }

    # Same rule for the docs ratchet: the wrapper maps exit 3 (docs unreadable) to a
    # failure (tests/test_docs_ratchet_gate.py pins it). Whole tree, never routed.
    Write-Host "==> docs ratchet (README size, history markers in docs)..." -ForegroundColor Cyan
    & $python -m scripts.docs_ratchet_gate
    if ($LASTEXITCODE -ne 0) { Fail "docs ratchet failed (README grew, a doc gained a history marker, or the docs could not be read)." }

    # Structure ratchet: fails on a file, function or directory over its limit, a
    # src/ module importing app/, or a root folder outside the declared shape, beyond
    # .structure-ratchet-baseline.json. Exit 3 (a file could not be measured, or an
    # unknown shape) is its own failure, never a pass or a skip. It reads the whole
    # tree, so it is not path-routed.
    Write-Host "==> structure ratchet (file size, function length, flat dirs, layers, root layout)..." -ForegroundColor Cyan
    & $python "scripts/structure_ratchet.py"
    $structureExit = $LASTEXITCODE
    if ($structureExit -eq 3) { Fail "structure ratchet NOT CONFIRMED (a file could not be measured, or the shape is unknown)." }
    if ($structureExit -ne 0) { Fail "structure ratchet failed (a new or grown offender, a root folder outside the shape, or a bad table or baseline)." }

    # Conventions ratchet: fleet conventions held as baselined `ast` rules. Call the
    # wrapper, never scripts/conventions_ratchet.py: the wrapper maps exit 3 (a file
    # could not be parsed) to a failure (tests/test_static_ratchet_gate.py pins
    # it). Whole tree, never routed.
    Write-Host "==> conventions ratchet (no-window, logging-not-print, no-drive-path)..." -ForegroundColor Cyan
    & $python -m scripts.conventions_ratchet_gate
    if ($LASTEXITCODE -ne 0) { Fail "conventions ratchet failed (a rule's hits rose, or a file could not be parsed)." }

    Write-Host "==> pytest (non-e2e)..." -ForegroundColor Cyan
    & $python -m pytest -q --ignore=tests/e2e
    if ($LASTEXITCODE -ne 0) { Fail "non-e2e pytest suite failed." }

    # Diff-proportionate e2e (#302): scripts/e2e_route.py wraps classify_e2e.py and
    # owns the fail-safe -- a router error, no verdict or an unknown tier is `full`,
    # never less. The only doubt left here is the wrapper dying before it prints a
    # tier, which also runs the whole suite. CI always runs the whole suite.
    $tier = "full"; $e2eTarget = "tests/e2e"; $e2eBrowsers = ""
    if ($env:CI -eq "true") {
        $routeReason = "CI always runs the full e2e suite"
    } else {
        $routeReason = "e2e router gave no verdict -- running the full suite (fail-safe)"
        $routeOut = & $python -m scripts.e2e_route
        $routeExit = $LASTEXITCODE
        $kv = @{}
        foreach ($line in $routeOut) {
            if ($line -match '^(E2E_[A-Z_]+)=(.*)$') { $kv[$matches[1]] = $matches[2] }
        }
        if ($routeExit -eq 0 -and $kv["E2E_TIER"]) {
            $tier = $kv["E2E_TIER"]
            $e2eTarget = $kv["E2E_PYTEST_TARGET"]
            $e2eBrowsers = $kv["E2E_BROWSERS"]
            $routeReason = $kv["E2E_REASON"]
        }
    }

    if ($tier -eq "skip") {
        Write-Host "==> pytest e2e: skipped, diff touches no e2e surface" -ForegroundColor Cyan
        Write-Host "    reason: $routeReason" -ForegroundColor DarkGray
        $e2eExit = 0
    } else {
        Write-Host "==> pytest e2e ($tier, Chromium + WebKit/iPhone unless narrowed, auto-booted)..." -ForegroundColor Cyan
        Write-Host "    reason: $routeReason" -ForegroundColor DarkGray
        # A `surface` slice over several modules arrives space-joined; pytest needs
        # each as its own argument. No browser named = the conftest's both projections.
        $e2eArgs = @($e2eTarget -split '\s+' | Where-Object { $_ })
        foreach ($b in ($e2eBrowsers -split ',' | Where-Object { $_ })) {
            $e2eArgs += @("--browser", $b)
        }
        $env:VT_E2E_AUTOBOOT = "1"
        try {
            # Per-test timing for /e2e-audit: the path is gitignored (.pytest_cache/)
            # and mirrors `[e2e] junit_xml` in .fleet.toml (#216).
            & $python -m pytest @e2eArgs -q --junitxml=.pytest_cache/e2e-junit.xml
            $e2eExit = $LASTEXITCODE
        }
        finally {
            Remove-Item Env:\VT_E2E_AUTOBOOT -ErrorAction SilentlyContinue
        }
    }
    if ($e2eExit -ne 0) { Fail "Playwright e2e suite failed." }
}
finally {
    Pop-Location
}

$sw.Stop()
Write-Host ""
Write-Host ("[OK] Ready to ship -- all checks passed in {0:n1}s." -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
exit 0
