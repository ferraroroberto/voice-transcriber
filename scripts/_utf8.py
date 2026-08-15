"""Shared UTF-8 stdout/stderr reconfigure for scripts/ that log emoji.

Piped/redirected stdout makes Python fall back to cp1252, so emoji log
lines raise ``UnicodeEncodeError`` when a script is run captured (e.g. from
``setup.bat``) or under a cp1252 console (global CLAUDE.md, "Windows
Python: UTF-8 stdout under capture"). Lifted out of ``set_password.py`` --
the one compliant call site -- so ``gen_ssl_cert.py``, ``gen_token.py`` and
``run_named_tunnel.py`` get the same fix instead of re-inlining it
(voice-transcriber#176).

Not a package import -- a plain sibling module; see ``_no_window.py``'s
docstring for the ``sys.path`` convention every caller follows.
"""

from __future__ import annotations

import sys


def reconfigure_utf8_streams() -> None:
    """Best-effort UTF-8 reconfigure of stdout and stderr so emoji log
    output survives a cp1252 console or a captured/redirected pipe."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
