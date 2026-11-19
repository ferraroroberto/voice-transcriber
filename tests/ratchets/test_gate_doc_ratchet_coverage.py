"""docs/testing.md's pre-ship gate paragraph must name every enabled ratchet.

Three places describe the gate (README.md, CLAUDE.md, docs/testing.md) and each
had drifted from `scripts/verify-before-ship.ps1` and from the others in a
different way (voice-transcriber#496): docs/testing.md was missing the runtime
write and usage track ratchet stages entirely. None of the vendored ratchets
catch this -- doc-refs only checks that a named path/module/port resolves, not
that a stage is named at all. This test counts the ratchet tables `.fleet.toml`
declares `enabled = true` and asserts docs/testing.md's gate paragraph names
exactly that many `**<name> ratchet**` stages, so dropping or forgetting one
fails loudly instead of silently drifting again.
"""

from __future__ import annotations

# Standard library imports
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FLEET_TOML = REPO_ROOT / ".fleet.toml"
TESTING_DOC = REPO_ROOT / "docs" / "testing.md"
_RATCHET_BOLD = re.compile(r"\*\*([A-Za-z]+(?: [A-Za-z]+)* ratchet)\*\*")


def _enabled_ratchet_tables() -> list[str]:
    """Top-level `.fleet.toml` tables named `*_ratchet` with `enabled = true`."""
    data = tomllib.loads(FLEET_TOML.read_text(encoding="utf-8"))
    return sorted(
        key for key, value in data.items()
        if key.endswith("_ratchet") and isinstance(value, dict) and value.get("enabled") is True
    )


def test_testing_doc_names_every_enabled_ratchet() -> None:
    enabled = _enabled_ratchet_tables()
    doc_text = TESTING_DOC.read_text(encoding="utf-8")
    named = _RATCHET_BOLD.findall(doc_text)
    assert len(named) == len(enabled), (
        "docs/testing.md's gate paragraph names "
        f"{len(named)} ratchet stage(s) ({named}) but .fleet.toml enables "
        f"{len(enabled)} ratchet table(s) ({enabled}) -- add or remove a "
        "`**<name> ratchet**` mention so the count matches."
    )
