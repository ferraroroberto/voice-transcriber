"""Shared Windows console-suppression helper for scripts/ subprocess spawns.

Every script under ``scripts/`` that shells out to an external executable
(tailscale, certutil, nvidia-smi, whisper-server --help, ...) needs
``creationflags=subprocess.CREATE_NO_WINDOW`` on win32 so a parent with no
console of its own (pythonw, a scheduled task, a fresh setup run) doesn't
flash a console window per spawn (fleet-config#412).

A thin re-export of ``src/no_window.py``'s ``NO_WINDOW`` (voice-transcriber#243)
-- that module is the one definition, consolidated instead of re-inlining the
``sys.platform`` ternary at every runtime call site. ``scripts/`` still needs
this sibling because it inserts its own directory onto ``sys.path``, not a
package import: each script's own directory (not the repo root) lands on
``sys.path[0]`` when run directly as ``python scripts/foo.py``, so ``src``
would not otherwise be importable. This module inserts the repo root itself
so that holds regardless of which script imports it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.no_window import NO_WINDOW  # noqa: E402


def no_window_kwargs() -> dict:
    """Extra ``subprocess.run``/``Popen`` kwargs that suppress the console
    window on Windows. Empty dict off Windows -- a harmless no-op there."""
    if NO_WINDOW:
        return {"creationflags": NO_WINDOW}
    return {}
