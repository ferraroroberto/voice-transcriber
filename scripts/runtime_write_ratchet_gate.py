r"""The gate's runtime-write stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.runtime_write_ratchet_gate

`scripts/runtime_write_ratchet.py` (with the resolver it imports,
`scripts/runtime_write_resolve.py`) is vendored and never edited here. Its exit 3 (a
new write target it cannot resolve, git unusable, or a tracked `.py` file that cannot
be parsed) means a write went unplaced; this stage reports it as NOT CONFIRMED and
fails. Exit 0 only when the script exited 0. `verify-before-ship.ps1` calls this
wrapper for the static stage and the script itself only for its --snapshot/--probe
pair around the test runs; the subprocess handling is `static_ratchet_gate.run`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate

SCRIPT = static_ratchet_gate.REPO_ROOT / "scripts" / "runtime_write_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# An `ast` pass plus static path resolution over the tracked Python files takes a few
# seconds; this only bounds a hung `git` child.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "a file gained a write under a code folder, or the baseline is missing",
    EXIT_CONFIG: "bad [runtime_write_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a new write target could not be resolved, so it was not checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no write under a code folder beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return static_ratchet_gate.run(
        script, name="runtime write ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit
    )


if __name__ == "__main__":
    sys.exit(run())
