"""Thin launcher — the only entrypoint meant to be invoked by humans / .bat files.

Usage:
    python scripts/launcher.py                 # same as `tray` — day-to-day default
    python scripts/launcher.py tray
    python scripts/launcher.py record
    python scripts/launcher.py gui
    python scripts/launcher.py server start|stop|status|logs
    python scripts/launcher.py transcribe <file>

The launcher puts the repo root (its parent folder) on `sys.path` so the top-level
packages (`src`, `app`, `scripts`) resolve without any outer namespace. That lets
the folder be the root of its own repository — clone it,
`pip install -r requirements.txt`, run the launcher.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
