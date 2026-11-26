"""The checklist's sign-in password item, run as `.fleet.toml` declares it.

The declared check runs for real against a throwaway clone, so a file that
holds no usable hash cannot read ``ok`` just by being there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src import installer, password
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
ITEM = "Sign-in password"


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A configured clone with no password set: the probe's module, its legacy file and a venv.

    The package ``__init__`` comes without the modules it imports, like a venv whose
    requirements are not installed: the probe must answer without importing the package.
    """
    root = tmp_path / "app"
    (root / "config").mkdir(parents=True)
    (root / "src").mkdir()
    for name in ("src/__init__.py", "src/password.py"):
        shutil.copy(REPO_ROOT / name, root / name)
    shutil.copy(REPO_ROOT / "config" / "webapp_config.sample.json", root / "config" / "webapp_config.json")
    venv_exe = installer.venv_python(root)
    venv_exe.parent.mkdir(parents=True)
    venv_exe.write_text("", encoding="utf-8")
    return root


def _run_probe(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    """Run the declared command with this interpreter, reading the clone's own password file."""
    env = {key: value for key, value in os.environ.items() if key != password.ENV_VAR}
    return subprocess.run(
        [sys.executable, *command[1:]], cwd=kwargs["cwd"], env=env, capture_output=True,
        timeout=kwargs["timeout"], check=False, creationflags=NO_WINDOW,
    )


def _state(root: Path) -> str:
    """The state the installer's checklist gives this repo's declared password item."""
    declared = [check for check in installer.load_plan(REPO_ROOT).checks if check.name == ITEM]
    report = installer.Report(what_if=False)
    installer.build_checklist(root, installer.Plan(checks=declared), report, _run_probe)
    (state,) = [state for state, item, _ in report.checklist if item == ITEM]
    return state


def _password_file(root: Path) -> Path:
    return root / "config" / "app_password.json"


def test_a_stored_hash_reads_ok(clone: Path) -> None:
    password.set_password("1234", _password_file(clone))
    assert _state(clone) == installer.OK


@pytest.mark.parametrize(
    "content",
    [None, "", "{not json", "{}", '{"password_hash": "plaintext"}'],
    ids=["no-file", "empty", "corrupt", "no-hash-key", "not-a-hash"],
)
def test_a_file_without_a_usable_hash_reads_missing(clone: Path, content: str | None) -> None:
    if content is not None:
        _password_file(clone).write_text(content, encoding="utf-8")
    assert _state(clone) == installer.MISSING


def test_an_unreadable_file_reads_unknown(clone: Path) -> None:
    # A folder at the file's path is there but cannot be read, on every platform.
    _password_file(clone).mkdir()
    assert _state(clone) == installer.UNKNOWN


def test_the_old_plaintext_password_still_reads_ok(clone: Path) -> None:
    # The login route honours it until a hash is stored, so the PC can sign in.
    (clone / "config" / "webapp_config.json").write_text(json.dumps({"auth_password": "old"}), encoding="utf-8")
    assert _state(clone) == installer.OK
