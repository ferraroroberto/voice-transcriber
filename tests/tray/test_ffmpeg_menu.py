"""The tray's ffmpeg items: shown only while ffmpeg is missing."""

from __future__ import annotations

# Standard library imports
import queue
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Third-party imports
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the tray is a Windows desktop surface")

_ITEMS = ["⚠ Audio conversion unavailable", "📋 Copy ffmpeg install command", "🔎 Check again"]


@pytest.fixture
def tray_module():
    from app.tray import tray

    return tray


def _app(tray_module, *, missing: bool):
    """A TrayApp with only what the menu and its two handlers read: no tk root, no hotkey."""
    app = tray_module.TrayApp.__new__(tray_module.TrayApp)
    app.config = MagicMock(hotkey_label="F8")
    app.services = MagicMock()
    app.services.webapp.config.enabled = False
    app.events = queue.Queue()
    app.append_mode = False
    app.ffmpeg_missing = missing
    app._icon = MagicMock()
    app._notify = MagicMock()
    return app


def _shown(menu) -> list[str]:
    """The labels a person sees: iterating a pystray menu skips hidden items."""
    return [item.text for item in menu if item.text in _ITEMS]


def test_the_items_follow_whether_ffmpeg_is_missing(tray_module) -> None:
    app = _app(tray_module, missing=True)
    menu = app._build_menu()
    assert _shown(menu) == _ITEMS
    assert not next(item for item in menu if item.text == _ITEMS[0]).enabled

    app.ffmpeg_missing = False

    assert _shown(menu) == []


def test_copy_puts_the_install_command_on_the_clipboard(tray_module, monkeypatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr(tray_module.pyperclip, "copy", copied.append)
    app = _app(tray_module, missing=True)

    app._handle_event(tray_module.EVT_COPY_FFMPEG_COMMAND)

    assert copied == ["winget install --id Gyan.FFmpeg --exact --source winget"]
    title, message = app._notify.call_args.args
    assert "PowerShell" in message and "Check again" in message


def test_check_again_hides_the_items_once_ffmpeg_is_found(tray_module, monkeypatch) -> None:
    found: list[Path | None] = [None]
    monkeypatch.setattr(tray_module, "find_ffmpeg", lambda _root=None: found[0])
    app = _app(tray_module, missing=True)
    menu = app._build_menu()

    app._handle_event(tray_module.EVT_CHECK_FFMPEG)
    assert _shown(menu) == _ITEMS

    found[0] = Path("ffmpeg.exe")
    app._handle_event(tray_module.EVT_CHECK_FFMPEG)

    assert _shown(menu) == []
    assert app._icon.update_menu.call_count == 2
