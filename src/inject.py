"""Caret injection: paste the active clipboard contents into the focused window.

Simulates ``Ctrl+V`` via ``pynput``'s keyboard controller, which the OS routes
to whatever currently has keyboard focus. Used by the tray after a hotkey
recording so the transcript lands directly in the user's app instead of
sitting on the clipboard.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def paste_at_caret(delay_before: float = 0.05) -> bool:
    """Send ``Ctrl+V`` to the focused window.

    Returns ``True`` on success, ``False`` if ``pynput`` isn't importable or
    the simulated keystroke raised. ``delay_before`` gives the OS clipboard a
    moment to commit a fresh ``pyperclip.copy(...)`` before paste fires —
    50 ms is enough on Windows in practice.
    """
    try:
        from pynput.keyboard import Controller, Key
    except ImportError:
        logger.warning("⚠️  pynput unavailable — skipping caret paste")
        return False

    if delay_before > 0:
        time.sleep(delay_before)

    try:
        kbd = Controller()
        with kbd.pressed(Key.ctrl):
            kbd.press("v")
            kbd.release("v")
    except Exception as exc:
        logger.warning(f"⚠️  paste_at_caret failed: {exc}")
        return False

    logger.info("📌 Pasted at caret")
    return True


def parse_simple_hotkey(hotkey: str):
    """Map a single-key hotkey string (e.g. ``<F8>``) to a pynput ``Key``.

    Returns ``None`` for modifier combos (``<ctrl>+<alt>+<space>``) or any
    unparseable input — callers fall back to plain toggle behaviour in that
    case, since "hold a 3-key chord for push-to-talk" is awkward UX.
    """
    try:
        from pynput.keyboard import Key
    except ImportError:
        return None
    if not hotkey or "+" in hotkey:
        return None
    token = hotkey.strip().lstrip("<").rstrip(">").lower()
    if not token:
        return None
    return getattr(Key, token, None)
