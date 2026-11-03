r"""Gate stage for the usage track ratchet: run the vendored script, label its exit code.

    & .\.venv\Scripts\python.exe -m scripts.usage_track_ratchet_gate

Exit 3 from `scripts/usage_track_ratchet.py` (with its reader `usage_track_scan.py`) means a
file was unreadable or its JS never closed, so nothing was checked; it fails here as NOT
CONFIRMED. The shared runner is `static_ratchet_gate.run`; `verify-before-ship.ps1` calls
this wrapper, never the script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "usage_track_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# A lexical pass over the tracked HTML and JS takes a few seconds; this only bounds a
# hung `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "a rule's hits rose above the baseline, the app does not reach its usage log, or the baseline is missing",
    EXIT_CONFIG: "bad [usage_track_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a file could not be read or closed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no untracked control, sheet or built id beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="usage track ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
