r"""The gate's conventions-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.conventions_ratchet_gate

`scripts/conventions_ratchet.py` (vendored, never edited here) exits 0 on a pass, 1
when a rule's hit count in a file rose or the baseline is missing, 2 on a bad table
or baseline and 3 when it could not check (git unusable, a tracked `.py` file that
cannot be read or parsed). Exit 3 means nothing was checked, so it fails the gate as
its own state and never reads as a pass. The subprocess handling is
`static_ratchet_gate.run`, shared so the ratchet stages cannot drift apart; this
module owns only the exit-code labels. `verify-before-ship.ps1` calls this wrapper,
never the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "conventions_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# An `ast` pass over the tracked Python files takes about a second; this only
# bounds a hung `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "a rule's hits rose above the baseline, or the baseline is missing",
    EXIT_CONFIG: "bad [conventions_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a file could not be read or parsed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no convention rule above its baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="conventions ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
