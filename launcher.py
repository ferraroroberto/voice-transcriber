"""Thin launcher — the only entrypoint meant to be invoked by humans / .bat files.

Usage:
    python launcher.py                 # same as `tray` — day-to-day default
    python launcher.py tray
    python launcher.py record
    python launcher.py gui
    python launcher.py server start|stop|status|logs
    python launcher.py transcribe <file>

The launcher puts its own folder on `sys.path` so the top-level packages
(`core`, `cli`, `gui`, `whisper_server`, `scripts`) resolve without any
outer namespace. That lets the folder be the root of its own repository
— clone it, `pip install -r requirements.txt`, run the launcher.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
