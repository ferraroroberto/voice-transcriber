"""The launchers live in ``scripts/``; the repo root keeps only ``tray.bat`` and ``setup.bat``.

Pins the pieces a move can silently break: the tray's process match (old and new spelling, so a
tray started before the move is still found and replaced), the tray's launch args, the repo-root
lookup of each moved launcher, and the launcher's usage line.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

ROOT = Path(__file__).resolve().parents[2]
MOVED = ("launcher.py", "webapp.bat", "webapp_tunnel_named.bat")
ROOT_LOOKUP = 'for %%I in ("%~dp0..") do set "SCRIPT_DIR=%%~fI\\"'


def _tray_bat() -> str:
    return (ROOT / "tray.bat").read_text(encoding="utf-8")


def _tray_match() -> re.Pattern[str]:
    found = re.search(r'-TrayMatch "([^"]+)"', _tray_bat())
    assert found, "tray.bat passes no -TrayMatch"
    return re.compile(found.group(1))


@pytest.mark.parametrize("name", MOVED)
def test_root_holds_none_of_the_moved_launchers(name):
    assert not (ROOT / name).exists()
    assert (ROOT / "scripts" / name).is_file()


@pytest.mark.parametrize(
    "command_line",
    [
        r"pythonw.exe launcher.py tray",  # a tray started before the move
        r"pythonw.exe scripts\launcher.py tray",
        "pythonw.exe scripts/launcher.py tray",
    ],
)
def test_tray_match_finds_both_spellings(command_line):
    assert _tray_match().search(command_line)


@pytest.mark.parametrize("command_line", [r"pythonw.exe scripts\launcher.py gui", "pythonw.exe other.py tray"])
def test_tray_match_ignores_other_processes(command_line):
    assert not _tray_match().search(command_line)


def test_tray_launches_the_moved_launcher():
    assert 'set "TRAY_LAUNCH=scripts\\launcher.py tray"' in _tray_bat()


@pytest.mark.parametrize("name", ["webapp.bat", "webapp_tunnel_named.bat", "server.bat"])
def test_moved_bat_finds_the_repo_root_again(name):
    assert ROOT_LOOKUP in (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_launcher_bad_command_prints_the_usage_line():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "launcher.py"), "no-such-command"],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=NO_WINDOW,
    )
    assert done.returncode != 0
    assert "usage:" in done.stderr.lower()
