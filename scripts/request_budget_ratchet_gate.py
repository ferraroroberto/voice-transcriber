r"""Gate stage for the request budget ratchet: run the vendored script, label its exit code.

    & .\.venv\Scripts\python.exe -m scripts.request_budget_ratchet_gate

Exit 3 from `scripts/request_budget_ratchet.py` (with its reader `request_budget_scan.py`)
means a file was unreadable or its JS or Python never closed or parsed, so nothing was
checked; it fails here as NOT CONFIRMED. The shared runner is `static_ratchet_gate.run`;
`verify-before-ship.ps1` calls this wrapper, never the script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "request_budget_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# One `git ls-files` and a lexical pass over the browser and server files; this only
# bounds a hung `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "a file's count for a rule rose above the baseline, or the baseline is missing",
    EXIT_CONFIG: "bad [request_budget_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a file could not be read or parsed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no request-budget hit beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="request budget ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
