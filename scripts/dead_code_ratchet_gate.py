r"""The gate's dead-code stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.dead_code_ratchet_gate

`scripts/dead_code_ratchet.py` is vendored and never edited here. Its exit 3 (git
unusable, or a tracked `.py` file that cannot be read or parsed) means no definition
was checked, and a file that does not parse may hold the only call of another; this
stage reports it as NOT CONFIRMED and fails. Exit 0 only when the script exited 0.
`verify-before-ship.ps1` calls this wrapper, never the script; the subprocess
handling is `static_ratchet_gate.run`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "dead_code_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# An `ast` pass over the tracked Python files takes about a second; this only bounds a
# hung `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "a function, class or method nothing references, or the baseline is missing",
    EXIT_CONFIG: "bad [dead_code_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a Python file could not be read or parsed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no unreferenced definition beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="dead code ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
