"""The browser take lifecycle is declared once, in ``take.js``.

Two kinds of check, both runnable in the gate without Vitest installed:

* **Source pins** keep ``state.mode`` written by ``take.js`` alone and keep
  every reader on ``isTake()`` instead of its own string comparison.
* **The machine itself** runs under Node (skipped when Node is absent): every
  legal move is taken and every other move is refused with a console error and
  no change. The recorder-level exits (self-stop, a throwing
  ``MediaRecorder``, a failed session create, a denied mic) are the Vitest
  table in ``app/webapp/static/__tests__/take.test.js``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static"
STATES = ["idle", "starting", "recording", "finishing"]
LEGAL = {
    ("idle", "starting"),
    ("starting", "recording"),
    ("starting", "idle"),
    ("recording", "finishing"),
    ("finishing", "idle"),
}
PATH_TO = {
    "idle": [],
    "starting": ["starting"],
    "recording": ["starting", "recording"],
    "finishing": ["starting", "recording", "finishing"],
}

# Runs take.js with a document stub (state.js looks elements up at import) and
# prints, for every (from, to) pair, whether the move was taken and the mode
# that followed.
_HARNESS = """
globalThis.document = { getElementById: () => null };
console.info = () => {};
const errors = [];
console.error = (m) => errors.push(String(m));
const { transition, isTake, TAKE_STATES } = await import('./take.js');
const { state } = await import('./state.js');
const paths = %(paths)s;
const out = [];
for (const from of Object.keys(paths)) {
  for (const to of Object.keys(TAKE_STATES)) {
    state.mode = 'idle';
    for (const step of paths[from]) transition(step, 'setup');
    errors.length = 0;
    const moved = transition(to, 'table row');
    out.push({ from, to, moved, now: state.mode, errors: errors.length });
  }
}
state.mode = 'idle';
errors.length = 0;
const unknown = transition('transcribing', 'typo');
out.push({ from: 'idle', to: 'transcribing', moved: unknown, now: state.mode, errors: errors.length });
console.log(JSON.stringify(out));
"""


def _run_machine() -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not on PATH")
    script = _HARNESS % {"paths": json.dumps(PATH_TO)}
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=str(STATIC_DIR),
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_take_machine_refuses_every_illegal_transition():
    """state-machine-proof: take"""
    rows = _run_machine()
    for row in rows:
        pair = (row["from"], row["to"])
        if pair in LEGAL:
            assert row["moved"] is True, row
            assert row["now"] == row["to"], row
            assert row["errors"] == 0, row
        else:
            assert row["moved"] is False, row
            assert row["now"] == row["from"], f"an illegal move changed the mode: {row}"
            assert row["errors"] == 1, f"an illegal move must log one console error: {row}"


def test_take_declares_the_four_states():
    source = (STATIC_DIR / "take.js").read_text(encoding="utf-8")
    declared = re.search(r"TAKE_STATES = Object\.freeze\(\{(.*?)\}\);", source, re.DOTALL)
    assert declared, "TAKE_STATES not found"
    assert re.findall(r"^\s+(\w+):", declared.group(1), re.MULTILINE) == STATES


def _modules() -> dict[str, str]:
    return {m.name: m.read_text(encoding="utf-8") for m in sorted(STATIC_DIR.glob("*.js"))}


def test_state_mode_is_written_only_by_take_js():
    for name, text in _modules().items():
        if name == "take.js":
            continue
        assert not re.search(r"\bstate\.mode\s*=[^=]", text), f"{name} writes state.mode"
        assert "setMode(" not in text, f"{name} still has a setMode()"


def test_no_reader_compares_state_mode_to_a_string():
    for name, text in _modules().items():
        if name == "take.js":
            continue
        assert not re.search(r"\bstate\.mode\b", text), (
            f"{name} reads state.mode directly; call isTake('<state>') from take.js"
        )
