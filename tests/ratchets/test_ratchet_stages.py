"""The gate's scaffold-ratchet stages map each vendored script's exit codes.

Each stage's script exits 3 when it could not check: nothing was checked. These tests
pin that 3 fails as NOT CONFIRMED -- never a pass, never a skip -- that the pre-ship
gate calls the wrapper that guarantees it rather than the script, and that the ratchet
is declared in `.fleet.toml` with its script vendored and its baseline committed. The
subprocess handling is the shared runner `static_ratchet_gate.run`, covered in
tests/test_static_ratchet_gate.py. They run fake scripts in a temp directory, never
the live tree.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

from tests.test_static_ratchet_gate import _fake_script

REPO_ROOT = Path(__file__).resolve().parents[2]

# name -> the gate wrapper module for each stage this module pins
STAGES = ["comment", "instruction", "doc_refs", "dead_code"]


def _wrapper(name: str):
    return importlib.import_module(f"scripts.{name}_ratchet_gate")


@pytest.mark.parametrize("name", STAGES)
def test_exit_three_is_not_confirmed_and_never_a_skip(name: str) -> None:
    gate = _wrapper(name)
    ok, label = gate.classify_exit(3)
    assert not ok
    assert "NOT CONFIRMED" in label
    assert "skip" not in label.lower()
    assert [gate.classify_exit(c)[0] for c in (0, 1, 2, 4, -1)] == [True] + [False] * 4


@pytest.mark.parametrize("code", [0, 1, 2, 3])
@pytest.mark.parametrize("name", STAGES)
def test_run_exits_nonzero_for_every_script_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, code: int
) -> None:
    got = _wrapper(name).run(_fake_script(tmp_path, code, "from the script"))
    out = capsys.readouterr().out
    assert got == (0 if code == 0 else 1)
    assert "from the script" in out  # the script's own finding is echoed
    last = out.rstrip().splitlines()[-1]
    label = name.replace("_", " ")
    assert last.startswith(f"OK: {label} ratchet" if code == 0 else f"FAIL: {label} ratchet")
    assert ("NOT CONFIRMED" in last) == (code == 3)


@pytest.mark.parametrize("name", STAGES)
def test_the_pre_ship_gate_calls_the_wrapper_not_the_script(name: str) -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    code_lines = [line for line in gate_ps1.splitlines() if not line.lstrip().startswith("#")]
    stage = [line for line in code_lines if f"{name}_ratchet" in line]
    assert len(stage) == 1
    assert f"-m scripts.{name}_ratchet_gate" in stage[0]
    assert f"{name}_ratchet.py" not in stage[0]


@pytest.mark.parametrize("name", STAGES)
def test_the_ratchet_is_declared_and_its_script_is_vendored(name: str) -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    table = fleet[f"{name}_ratchet"]
    assert table["enabled"] is True
    assert (REPO_ROOT / table["baseline"]).is_file()
    entry = fleet["vendored"][f"{name}_ratchet"]
    assert entry["dest"] == f"scripts/{name}_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
