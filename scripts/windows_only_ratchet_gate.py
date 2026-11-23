r"""Gate stage for the windows-only ratchet: run the vendored script, label its exit code.

    & .\.venv\Scripts\python.exe -m scripts.windows_only_ratchet_gate

Exit 3 from `scripts/windows_only_ratchet.py` means a test module could not be listed,
read or parsed, so nothing was checked; it fails here as NOT CONFIRMED. The shared runner
is `static_ratchet_gate.run`; `verify-before-ship.ps1` calls this wrapper, never the script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "windows_only_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# One `git ls-files` and an `ast` pass over the test modules; this only bounds a hung
# `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "a file's count for a rule rose above the baseline, or the baseline is missing",
    EXIT_CONFIG: "bad [windows_only_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a test module could not be read or parsed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no unmarked Windows-only test beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="windows only ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
