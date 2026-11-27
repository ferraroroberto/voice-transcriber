"""Locate ffmpeg, and hold the one command that installs it.

The tray, the web app, the transcode error and the installer checklist
(``.fleet.toml [[install.check]]``) all hand out :data:`FFMPEG_INSTALL_COMMAND`. The
app only ever shows or copies it: running an installer is the person's call.

``python -P src/ffmpeg_tool.py`` is the installer's probe: exit 0 when ffmpeg
is found, 1 when it is not. It runs as a file, so this module imports the
standard library only.
"""

from __future__ import annotations

# Standard library imports
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

FFMPEG_INSTALL_COMMAND = "winget install --id Gyan.FFmpeg --exact --source winget"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_ffmpeg(project_root: Optional[Path] = None) -> Optional[Path]:
    """Return the absolute path to a usable ffmpeg, or ``None``.

    Looks on ``PATH``, then in ``vendor/ffmpeg/``, then where winget puts it.
    Every call looks again, so an install made while the app runs is found.
    """
    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)

    vendor = (project_root or PROJECT_ROOT) / "vendor" / "ffmpeg"
    bundled = vendor / "ffmpeg.exe"
    if not bundled.exists() and sys.platform != "win32":
        bundled = vendor / "ffmpeg"
    if bundled.exists():
        return bundled
    return _find_winget_ffmpeg()


def _find_winget_ffmpeg() -> Optional[Path]:
    """The ffmpeg a per-user winget install left behind.

    A running process keeps the ``PATH`` it started with, so ``PATH`` alone
    misses a fresh install until the app restarts. winget links portable
    programs from its Links folder; where it cannot create the link it only
    unpacks the package, so that folder is searched too.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if sys.platform != "win32" or not local_app_data:
        return None
    winget = Path(local_app_data) / "Microsoft" / "WinGet"
    link = winget / "Links" / "ffmpeg.exe"
    if link.is_file():
        return link
    builds = sorted(winget.glob("Packages/Gyan.FFmpeg_*/*/bin/ffmpeg.exe"))
    return builds[-1] if builds else None


if __name__ == "__main__":
    sys.exit(0 if find_ffmpeg() else 1)
