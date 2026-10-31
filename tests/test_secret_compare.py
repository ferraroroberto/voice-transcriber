"""Secret comparison accepts any text, and only `src.secret_compare` does it.

`hmac.compare_digest` raises `TypeError` for a non-ASCII `str`, which turned a
mistyped accent in a password or bearer token into a 500. The structural test
keeps a new call site from reintroducing the raw call.
"""

from __future__ import annotations

# Standard library imports
import ast
from pathlib import Path

# Local imports
from src.secret_compare import secrets_equal

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "src" / "secret_compare.py"
SCANNED_ROOTS = ("app", "src", "scripts")


def test_equal_ascii_and_non_ascii():
    assert secrets_equal("letmein", "letmein")
    assert secrets_equal("passñword", "passñword")


def test_unequal_never_raises():
    assert not secrets_equal("letmein", "letmèin")
    assert not secrets_equal("", "letmein")
    assert not secrets_equal("é", "e")


def test_only_the_helper_calls_compare_digest():
    offenders = []
    for root in SCANNED_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if path == HELPER or ".venv" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "compare_digest"
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "call src.secret_compare.secrets_equal instead of compare_digest: "
        + ", ".join(offenders)
    )
