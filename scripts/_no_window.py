"""Shared Windows console-suppression helper for scripts/ subprocess spawns.

Every script under ``scripts/`` that shells out to an external executable
(tailscale, certutil, nvidia-smi, whisper-server --help, ...) needs
``creationflags=subprocess.CREATE_NO_WINDOW`` on win32 so a parent with no
console of its own (pythonw, a scheduled task, a fresh setup run) doesn't
flash a console window per spawn (fleet-config#412). Consolidated here
instead of re-inlining the ternary at every call site (voice-transcriber#176)
-- ``run_named_tunnel.py`` already did this inline for its one long-lived
``Popen``; the rest of ``scripts/`` used bare ``subprocess.run`` with no flag
at all.

Not a package import -- a plain sibling module. Each script inserts its own
directory onto ``sys.path`` before importing this (works whether the script
runs directly as ``python scripts/foo.py``, whose interpreter already does
this automatically, or is loaded by path -- e.g. via
``importlib.util.spec_from_file_location`` in a test -- which does not).
"""

from __future__ import annotations

import subprocess
import sys


def no_window_kwargs() -> dict:
    """Extra ``subprocess.run``/``Popen`` kwargs that suppress the console
    window on Windows. Empty dict off Windows -- a harmless no-op there."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
