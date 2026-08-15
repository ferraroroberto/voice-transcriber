"""Standalone webapp launcher for ``webapp.bat`` -- headless boxes, dev
iteration, or running the webapp without the tray icon / global hotkey.

Builds the exact same ``-m uvicorn ...`` argv the tray's ``WebappManager``
uses via ``app.webapp.manager.build_uvicorn_command`` (voice-transcriber#160)
and runs it in the foreground, inheriting this console. ``webapp.bat``
shells out to this module instead of hand-writing the uvicorn argv itself,
so a flag added to the tray's spawn path is never missing here
(voice-transcriber#174) -- it had already drifted (missing
``--log-level warning``) before this consolidation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Run directly as `python scripts/run_webapp.py` (see webapp.bat), so
# sys.path[0] is this script's own directory, not the repo root -- `app`
# isn't importable without this.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.webapp.manager import build_uvicorn_command, cert_paths  # noqa: E402

HOST = "0.0.0.0"
PORT = 8443


def main() -> int:
    certs = cert_paths(PROJECT_ROOT)
    if certs is None:
        print("[INFO] No HTTPS cert found, running HTTP-only on :8443.")
        print(
            "       Run scripts\\gen_ssl_cert.py to enable HTTPS "
            "(required for mobile mics)."
        )
    else:
        print(f"[INFO] HTTPS via {certs[0]}")

    cmd = [sys.executable] + build_uvicorn_command(HOST, PORT, certs)
    # Foreground, console-visible spawn -- webapp.bat is a deliberately
    # interactive terminal session, so this inherits stdio rather than
    # suppressing the console window.
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode


if __name__ == "__main__":
    sys.exit(main())
