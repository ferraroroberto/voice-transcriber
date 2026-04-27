"""Resident tray app with global hotkey.

- Tray icon sits in the system tray.
- Menu: Record / Open / Start server / Stop server / Quit.
- Global hotkey (default Ctrl+Alt+Space) starts a recording immediately; a
  second press stops it. Transcribed text is copied to the clipboard and a
  toast is shown via the tray.
- Closing the (optional) main window hides it; only "Quit" from the tray
  menu really exits — and that's when we stop the server if we own it.
"""

from __future__ import annotations

# Standard library imports
import atexit
import logging
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

# Third-party imports
import psutil
import pyperclip
from PIL import Image, ImageDraw
import pystray
from pynput import keyboard

try:
    from winotify import Notification as _WinToast  # type: ignore
except ImportError:  # non-Windows or package missing
    _WinToast = None

from core import (
    AppConfig,
    AudioRecorder,
    TranscriptionClient,
    TranscriptionError,
)
from core.recorder import Recording
from whisper_server import OWNERSHIP_OURS, WhisperServerManager
from .app import TranscriberApp
from .recording_popup import RecordingPopup

logger = logging.getLogger(__name__)

# Queue events dispatched on the tkinter main thread.
EVT_TOGGLE_RECORD = "toggle_record"
EVT_OPEN_WINDOW = "open_window"
EVT_START_SERVER = "start_server"
EVT_STOP_SERVER = "stop_server"
EVT_MODEL_INFO = "model_info"
EVT_QUIT = "quit"


class TrayApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.server = WhisperServerManager()
        self.events: queue.Queue = queue.Queue()

        # Hidden root so Toplevel popups and after() work.
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self._main_window: Optional[TranscriberApp] = None
        self._recording_popup: Optional[RecordingPopup] = None
        self._current_recorder: Optional[AudioRecorder] = None
        self._icon: Optional[pystray.Icon] = None
        self._hotkey_listener: Optional[keyboard.GlobalHotKeys] = None
        # Latest non-empty transcription, surfaced in the main window so the
        # user can re-copy it after the clipboard has been overwritten.
        self.last_transcription: Optional[str] = None

    # ------------------------------------------------------------ run / quit

    def run(self) -> int:
        self._start_hotkey_listener()
        self._icon = pystray.Icon(
            "transcribe_voice",
            _make_icon_image(color=(70, 180, 120)),
            "Voice Transcription",
            menu=self._build_menu(),
        )
        self._icon.run_detached()
        self.root.after(100, self._pump_events)
        logger.info(f"🧷 Tray ready — hotkey: {self.config.hotkey}")
        if not self.server.status().running:
            threading.Thread(target=self._start_server_worker, daemon=True).start()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _shutdown(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        status = self.server.status()
        if status.running and status.ownership == OWNERSHIP_OURS:
            self._notify("Whisper server", "🛑 Stopped")
            # winotify dispatches the toast via a powershell subprocess; give
            # it a moment to spawn before os._exit(0) tears everything down.
            time.sleep(0.5)
            self.server.stop()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        # pystray's Windows message-pump thread and pynput's low-level keyboard
        # hook sometimes fail to unwind after stop(), leaving pythonw.exe alive.
        # Force-exit once we've cleanly stopped the server we own.
        os._exit(0)

    # --------------------------------------------------------------- menu

    def _build_menu(self) -> pystray.Menu:
        record_label = f"🎤 Record / Stop  ({self.config.hotkey_label})"
        # Compute the model label lazily so it refreshes on every menu open —
        # pystray re-evaluates callable text each time the menu is drawn.
        def model_label(_item) -> str:
            try:
                desc = self.server.describe()
                return f"🧠 {desc.model_display_name}"
            except Exception:
                return "🧠 model: ?"

        return pystray.Menu(
            pystray.MenuItem(model_label, None, enabled=False),
            pystray.MenuItem("ℹ Model info…", lambda: self._enqueue(EVT_MODEL_INFO)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(record_label, lambda: self._enqueue(EVT_TOGGLE_RECORD), default=True),
            pystray.MenuItem("🪟 Open window", lambda: self._enqueue(EVT_OPEN_WINDOW)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("▶ Start server", lambda: self._enqueue(EVT_START_SERVER)),
            pystray.MenuItem("■ Stop server", lambda: self._enqueue(EVT_STOP_SERVER)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self._enqueue(EVT_QUIT)),
        )

    # ---------------------------------------------------------- hotkey

    def _start_hotkey_listener(self) -> None:
        hotkey = self.config.hotkey
        try:
            mapping = {hotkey: lambda: self._enqueue(EVT_TOGGLE_RECORD)}
            self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
            self._hotkey_listener.start()
        except Exception as e:
            logger.error(f"❌ Failed to register global hotkey {hotkey!r}: {e}")

    # --------------------------------------------------------- event loop

    def _enqueue(self, event: str) -> None:
        self.events.put(event)

    # Public hooks used by the main window when it's opened from the tray,
    # so the two paths share one recorder / hotkey / notification pipeline.
    def request_toggle_record(self) -> None:
        self._enqueue(EVT_TOGGLE_RECORD)

    def request_quit(self) -> None:
        self._enqueue(EVT_QUIT)

    def _pump_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(80, self._pump_events)

    def _handle_event(self, event: str) -> None:
        if event == EVT_TOGGLE_RECORD:
            self._toggle_record()
        elif event == EVT_OPEN_WINDOW:
            self._open_window()
        elif event == EVT_START_SERVER:
            threading.Thread(target=self._start_server_worker, daemon=True).start()
        elif event == EVT_STOP_SERVER:
            threading.Thread(target=self.server.stop, daemon=True).start()
        elif event == EVT_MODEL_INFO:
            self._show_model_info()
        elif event == EVT_QUIT:
            self.root.quit()

    def _show_model_info(self) -> None:
        """Surface model/memory/runtime info either through the open main
        window's details dialog or, if no window exists, via a toast with
        the one-line summary.
        """
        if self._main_window is not None and self._main_window.root.winfo_exists():
            try:
                self._main_window._show_model_details()
                return
            except Exception as exc:
                logger.debug(f"details dialog failed, falling back to toast: {exc}")
        description = self.server.describe()
        self._notify(
            f"🧠 {description.model_display_name}",
            description.summary_line(),
        )

    # ---------------------------------------------------- record handling

    def _toggle_record(self) -> None:
        # Second press → stop.
        if self._current_recorder is not None:
            self._current_recorder.request_stop()
            return

        status = self.server.status()
        if not status.running:
            self._notify("Server not running", "Start the whisper-server from the tray menu first.")
            return

        recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            preferred_mics=self.config.resolve_preferred_mics(),
        )
        self._current_recorder = recorder
        self._set_icon_color(recording=True)

        self._recording_popup = RecordingPopup(
            parent=self.root,
            recorder=recorder,
            max_seconds=self.config.max_record_seconds,
            on_done=self._on_record_done,
            hotkey_label=self.config.hotkey_label,
        )

    def _on_record_done(self, recording: Optional[Recording], error: Optional[str]) -> None:
        self._current_recorder = None
        self._recording_popup = None
        self._set_icon_color(recording=False)
        if error is not None:
            self._notify("Recording error", error)
            return
        if recording is None:
            return
        threading.Thread(target=self._transcribe_worker, args=(recording,), daemon=True).start()

    def _transcribe_worker(self, recording: Recording) -> None:
        status = self.server.status()
        client = TranscriptionClient(status.base_url)
        try:
            iso_lang = self.config.whisper_language
            text = client.transcribe_array(
                recording.samples, recording.sample_rate,
                language=iso_lang,
            )
        except TranscriptionError as e:
            self._notify("Transcription failed", str(e))
            return

        text = text.strip()
        if not text:
            self._notify("Empty transcription", "The server returned no text.")
            return

        self.last_transcription = text

        if self.config.auto_copy:
            try:
                pyperclip.copy(text)
            except Exception as exc:
                logger.warning(f"⚠️  Clipboard copy failed: {exc}")

        preview = text if len(text) <= 80 else text[:77] + "…"
        self._notify("📋 Copied to clipboard", preview)

    # -------------------------------------------------------- window / server

    def _open_window(self) -> None:
        if self._main_window is None or not self._main_window.root.winfo_exists():
            self._main_window = TranscriberApp(self.config, tray_on_close=True, tray=self)
            # Re-parent its close to just hide.
            self._main_window.root.protocol("WM_DELETE_WINDOW", self._main_window._on_close)
        else:
            self._main_window.root.deiconify()
            self._main_window.root.lift()

    def _start_server_worker(self) -> None:
        try:
            self.server.start()
            self._notify("Whisper server", "✅ Running")
        except RuntimeError as e:
            self._notify("Server failed to start", str(e))

    # -------------------------------------------------------- ui affordances

    def _set_icon_color(self, recording: bool) -> None:
        if self._icon is None:
            return
        color = (220, 60, 60) if recording else (70, 180, 120)
        try:
            self._icon.icon = _make_icon_image(color=color)
        except Exception:
            pass

    def _notify(self, title: str, message: str) -> None:
        # Prefer modern WinRT toasts — they stack in Action Center instead of
        # being coalesced like the legacy Shell_NotifyIcon balloon tips that
        # pystray uses, so rapid-fire notifications don't get dropped.
        if _WinToast is not None:
            try:
                toast = _WinToast(
                    app_id="Voice Transcription",
                    title=title,
                    msg=message,
                )
                toast.show()
                return
            except Exception as exc:
                logger.debug(f"winotify failed, falling back to pystray: {exc}")
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
                return
            except Exception:
                pass
        logger.info(f"🔔 {title}: {message}")


# --------------------------------------------------------------------- icon

def _make_icon_image(color=(70, 180, 120)) -> Image.Image:
    """Draw a simple microphone glyph as the tray icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Body
    draw.rounded_rectangle((22, 10, 42, 40), radius=10, fill=color)
    # Stand
    draw.rectangle((30, 44, 34, 54), fill=color)
    draw.rectangle((20, 52, 44, 56), fill=color)
    # Mic grill
    for y in (18, 24, 30):
        draw.line((26, y, 38, y), fill=(255, 255, 255, 180), width=1)
    return img


_TRAY_LOCK_FILE = Path(__file__).resolve().parent.parent / ".tray.pid"


def _acquire_single_instance_lock() -> bool:
    """Return True if we got the lock, False if another tray is already running.

    Uses a PID file validated against psutil so a stale lock from a crashed
    process doesn't permanently lock us out.
    """
    if _TRAY_LOCK_FILE.exists():
        try:
            pid = int(_TRAY_LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            pid = None
        if pid and psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                cmdline = " ".join(proc.cmdline()).lower()
                if "launcher.py" in cmdline and "tray" in cmdline:
                    logger.warning(f"⚠️  Tray already running (pid {pid}); exiting.")
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    _TRAY_LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_single_instance_lock)
    return True


def _release_single_instance_lock() -> None:
    try:
        if _TRAY_LOCK_FILE.exists() and _TRAY_LOCK_FILE.read_text().strip() == str(os.getpid()):
            _TRAY_LOCK_FILE.unlink()
    except OSError:
        pass


def run_tray(config: AppConfig) -> int:
    if not _acquire_single_instance_lock():
        return 0
    return TrayApp(config).run()
