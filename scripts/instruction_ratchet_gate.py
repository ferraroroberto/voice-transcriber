r"""The gate's instruction-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.instruction_ratchet_gate

`scripts/instruction_ratchet.py` is vendored and never edited here. Its exit 3 (git
unusable, an instruction file that cannot be read, a code fence that never closes)
means no rule was checked; this stage reports it as NOT CONFIRMED and fails. Exit 0
only when the script exited 0. `verify-before-ship.ps1` calls this wrapper, never the
script; the subprocess handling is `static_ratchet_gate.run`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "instruction_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# Two or three Markdown files linted line by line take well under a second; this only
# bounds a hung `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "an instruction rule's count rose or a file passed its size cap, or the baseline is missing",
    EXIT_CONFIG: "bad [instruction_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- an instruction file could not be read, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no instruction rule above its baseline, every file within its size cap"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="instruction ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
