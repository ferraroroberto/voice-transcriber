"""Guard: a path or module a committed sample config names must exist.

The doc-refs ratchet scans README/CLAUDE.md/docs only, so a dead
``scripts/<name>.py`` or ``python -m <module>`` inside a ``config/*.sample.json``
comment would otherwise go unnoticed until a fresh install follows it.
"""

from __future__ import annotations

# Standard library imports
import json
import re
from pathlib import Path

# Third-party imports
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = sorted((PROJECT_ROOT / "config").glob("*.sample.json"))

_SCRIPT_RE = re.compile(r"\bscripts/[\w./-]+\.(?:py|ps1|bat)\b")
_MODULE_RE = re.compile(r"python -m ([\w.]+)")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda p: p.name)
def test_sample_config_references_resolve(sample: Path) -> None:
    data = json.loads(sample.read_text(encoding="utf-8"))
    missing = []
    for text in _strings(data):
        for rel in _SCRIPT_RE.findall(text):
            if not (PROJECT_ROOT / rel).is_file():
                missing.append(rel)
        for module in _MODULE_RE.findall(text):
            if not (PROJECT_ROOT / (module.replace(".", "/") + ".py")).is_file():
                missing.append(f"python -m {module}")
    assert not missing, f"{sample.name} names files that do not exist: {missing}"
