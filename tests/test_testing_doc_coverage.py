"""docs/testing.md's "What each file covers" table must list every tracked test module.

The table is hand-maintained prose: a new `tests/**/test_*.py` file can land with
no row and nothing else notices (voice-transcriber#343 found 14 modules missing).
The vendored docs ratchet does not catch this -- it only flags README size and
history markers (issue refs, dated/changelog headings), never missing table rows.
This test enumerates every tracked test module via `git ls-files` and asserts each
one appears, as the table's own Windows-backslash backtick-quoted path, in the doc.
"""

from __future__ import annotations

# Standard library imports
import re
import subprocess
import sys
from pathlib import Path

# Third-party imports
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTING_DOC = REPO_ROOT / "docs" / "testing.md"
GIT_TIMEOUT_S = 30
_TEST_MODULE = re.compile(r"test_.*\.py$")
# Windows CREATE_NO_WINDOW keeps this spawn console-less, same as every
# vendored ratchet script's own git invocation.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _tracked_test_modules() -> list[str]:
    """Every tracked `tests/**/test_*.py` path, forward-slashed, sorted."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=GIT_TIMEOUT_S,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        pytest.fail(f"git ls-files exited {proc.returncode}: {proc.stderr!r}")
    names = proc.stdout.decode("utf-8").split("\0")
    return sorted(name for name in names if name and _TEST_MODULE.search(name))


def test_every_tracked_test_module_has_a_coverage_row() -> None:
    doc_text = TESTING_DOC.read_text(encoding="utf-8")
    missing = [
        module for module in _tracked_test_modules()
        if module.replace("/", "\\") not in doc_text
    ]
    assert not missing, (
        "docs/testing.md's \"What each file covers\" table is missing a row "
        f"for: {', '.join(missing)}"
    )
