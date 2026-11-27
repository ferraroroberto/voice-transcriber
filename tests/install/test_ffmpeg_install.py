"""Finding ffmpeg after a winget install, and the checklist item that offers it."""

from __future__ import annotations

# Standard library imports
import os
import subprocess
import sys
from pathlib import Path

# Third-party imports
import pytest

# Local imports
from src import ffmpeg_tool, installer
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="winget installs ffmpeg on Windows only")


def _plant(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


@pytest.fixture
def winget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A faked per-user WinGet folder on a PC with ffmpeg neither on PATH nor in vendor/."""
    monkeypatch.setattr(ffmpeg_tool.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path / "local" / "Microsoft" / "WinGet"


@_WINDOWS_ONLY
def test_an_install_into_the_links_folder_is_found_by_the_next_look(winget: Path, tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    assert ffmpeg_tool.find_ffmpeg(app_root) is None

    link = _plant(winget / "Links" / "ffmpeg.exe")

    assert ffmpeg_tool.find_ffmpeg(app_root) == link


@_WINDOWS_ONLY
def test_an_unlinked_gyan_package_is_found(winget: Path, tmp_path: Path) -> None:
    package = winget / "Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_abc"
    exe = _plant(package / "ffmpeg-1.0-full_build" / "bin" / "ffmpeg.exe")

    assert ffmpeg_tool.find_ffmpeg(tmp_path / "app") == exe


@_WINDOWS_ONLY
def test_the_installer_probe_runs_as_a_file_and_finds_ffmpeg_on_path(tmp_path: Path) -> None:
    """The declared command itself, so an import it cannot satisfy as a file shows up."""
    check = _declared_check()
    _plant(tmp_path / "bin" / "ffmpeg.exe")
    command = [sys.executable if part == installer.PYTHON_TOKEN else part for part in check.command]

    done = subprocess.run(
        command, cwd=REPO_ROOT, env={**os.environ, "PATH": str(tmp_path / "bin")},
        capture_output=True, timeout=30, check=False, creationflags=NO_WINDOW,
    )

    assert done.returncode == 0, done.stderr


def _declared_check() -> installer.Check:
    (check,) = [c for c in installer.load_plan(REPO_ROOT).checks if c.name == "ffmpeg"]
    return check


def test_the_checklist_offers_ffmpeg_as_optional_with_the_one_command() -> None:
    check = _declared_check()

    assert check.level == installer.OPTIONAL
    assert check.hint == f"run: {ffmpeg_tool.FFMPEG_INSTALL_COMMAND}"
    assert ffmpeg_tool.FFMPEG_INSTALL_COMMAND == "winget install --id Gyan.FFmpeg --exact --source winget"
