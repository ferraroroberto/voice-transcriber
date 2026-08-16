"""Resident tray app with global hotkey.

- Tray icon sits in the system tray.
- Menu: Record / Open / Start server / Stop server / Quit.
- Global hotkey (default F8) supports both tap-toggle and push-to-talk on
  the same key: tap once to start, tap again to stop; or hold for ≥
  ``ptt_threshold_ms`` and release to stop. Transcribed text is copied to
  the clipboard and (when ``auto_paste_after_hotkey`` is on) pasted at the
  caret via Ctrl+V into the focused window. Modifier-combo hotkeys
  (e.g. ``<ctrl>+<alt>+<space>``) fall back to toggle-only.
- Closing the (optional) main window hides it; only "Quit" from the tray
  menu really exits — and that's when we stop the server if we own it.

The PTT hotkey state machine and the three background service lifecycles
(whisper-server, uvicorn webapp, cloudflared tunnel) each live in their
own class (``HotkeyController``, ``ServiceSupervisor`` —
voice-transcriber#177); ``TrayApp`` composes them plus owns the pystray
icon/menu, the tkinter event pump, and the record → transcribe → notify
pipeline. ``TranscriberApp`` (the optional main window) only reaches
``TrayApp`` through its narrow public surface — ``get_last_transcription``/
``set_last_transcription``, ``append_mode``/``set_append_mode``/
``add_append_listener``/``remove_append_listener``,
``request_toggle_record``/``request_quit`` — never through private
attributes.
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

# Third-party imports
import pyperclip
from PIL import Image
import pystray

try:
    from winotify import Notification as _WinToast  # type: ignore
except ImportError:  # non-Windows or package missing
    _WinToast = None

from src import (
    AppConfig,
    AudioRecorder,
    TranscriptionClient,
    build_transcription_client,
)
from src.inject import paste_at_caret
from src.mic_glyph import draw_mic
from src.recorder import Recording
from src.recording_pipeline import handle_take
from src.webapp_config import append_auth_token, load_webapp_config
from app.tray.single_instance import SingleInstance
from .app import TranscriberApp
from .hotkey_controller import HotkeyController
from .recording_popup import RecordingPopup
from .service_supervisor import ServiceSupervisor, current_auth_token

logger = logging.getLogger(__name__)

# Queue events dispatched on the tkinter main thread.
EVT_TOGGLE_RECORD = "toggle_record"
EVT_OPEN_WINDOW = "open_window"
EVT_START_SERVER = "start_server"
EVT_STOP_SERVER = "stop_server"
EVT_MODEL_INFO = "model_info"
EVT_COPY_LOCAL_URL = "copy_local_url"
EVT_COPY_TUNNEL_URL = "copy_tunnel_url"
EVT_RESTART_WEBAPP = "restart_webapp"
EVT_TOGGLE_APPEND = "toggle_append"
EVT_TOGGLE_AUTO_PASTE = "toggle_auto_paste"
EVT_TOGGLE_SUPPRESS_HOTKEY = "toggle_suppress_hotkey"
EVT_TOGGLE_NOTIFICATIONS = "toggle_notifications"
EVT_QUIT = "quit"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TUNNEL_CONFIG_PATH = PROJECT_ROOT / "webapp" / "cloudflared.yml"
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"


class TrayApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events: queue.Queue = queue.Queue()

        # Hidden root so Toplevel popups and after() work.
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self._main_window: Optional[TranscriberApp] = None
        self._recording_popup: Optional[RecordingPopup] = None
        self._current_recorder: Optional[AudioRecorder] = None
        self._icon: Optional[pystray.Icon] = None
        self._transcription_client: Optional[TranscriptionClient] = None
        # Latest non-empty transcription, surfaced in the main window so the
        # user can re-copy it after the clipboard has been overwritten.
        self.last_transcription: Optional[str] = None
        # Append mode: when True, each new take is glued onto the previous
        # transcript with a blank-line separator instead of replacing it.
        # Ephemeral (off on every launch). Mirrored by the tk window's
        # checkbox so toggling either surface stays in sync.
        self.append_mode: bool = False
        self._append_listeners: list = []
        # Translate-to-English toggle — mirrored from the tk window's
        # translate_var (voice-transcriber#178) so a mic take routed through
        # the tray (the default whenever the window was opened from it)
        # honours the checkbox instead of silently ignoring it. Ephemeral,
        # off on every launch, same as append_mode.
        self.translate: bool = False

        # PTT hotkey state machine (voice-transcriber#177).
        self._hotkeys = HotkeyController(
            config,
            has_active_recorder=lambda: self._current_recorder is not None,
            enqueue_toggle=lambda: self._enqueue(EVT_TOGGLE_RECORD),
        )
        # Whisper-server / webapp / cloudflared-tunnel lifecycles (voice-transcriber#177).
        self.services = ServiceSupervisor(
            config,
            tunnel_config_path=TUNNEL_CONFIG_PATH,
            tunnel_url_file=TUNNEL_URL_FILE,
            project_root=PROJECT_ROOT,
            notify=self._notify,
        )

    # ------------------------------------------------------------ run / quit

    def run(self) -> int:
        self._hotkeys.start()
        self._icon = pystray.Icon(
            "transcribe_voice",
            _make_icon_image(color=(70, 180, 120)),
            "Voice Transcription",
            menu=self._build_menu(),
        )
        self._icon.run_detached()
        self.root.after(100, self._pump_events)
        logger.info(f"🧷 Tray ready — hotkey: {self.config.hotkey}")
        self.services.start_all()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _shutdown(self) -> None:
        self._hotkeys.stop()
        self.services.stop_all()
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

        items = [
            pystray.MenuItem(lambda _item: self.services.model_label(), None, enabled=False),
            pystray.MenuItem("ℹ Model info…", lambda: self._enqueue(EVT_MODEL_INFO)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(record_label, lambda: self._enqueue(EVT_TOGGLE_RECORD)),
            pystray.MenuItem("🪟 Open window", lambda: self._enqueue(EVT_OPEN_WINDOW)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("▶ Start server", lambda: self._enqueue(EVT_START_SERVER)),
            pystray.MenuItem("■ Stop server", lambda: self._enqueue(EVT_STOP_SERVER)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "➕ Append mode",
                lambda: self._enqueue(EVT_TOGGLE_APPEND),
                checked=lambda _item: self.append_mode,
            ),
            pystray.MenuItem(
                "📌 Paste at caret",
                lambda: self._enqueue(EVT_TOGGLE_AUTO_PASTE),
                checked=lambda _item: self.config.auto_paste_after_hotkey,
            ),
            pystray.MenuItem(
                "🚫 Suppress hotkey",
                lambda: self._enqueue(EVT_TOGGLE_SUPPRESS_HOTKEY),
                checked=lambda _item: self.config.suppress_hotkey,
            ),
            pystray.MenuItem(
                "🔔 Show notifications",
                lambda: self._enqueue(EVT_TOGGLE_NOTIFICATIONS),
                checked=lambda _item: self.config.show_notifications,
            ),
        ]
        if self.services.webapp.config.enabled:
            items.extend([
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda _item: self.services.webapp_label(), None, enabled=False,
                ),
                pystray.MenuItem(
                    "📋 Copy local URL",
                    lambda: self._enqueue(EVT_COPY_LOCAL_URL),
                ),
                pystray.MenuItem(
                    "📋 Copy Cloudflare URL",
                    lambda: self._enqueue(EVT_COPY_TUNNEL_URL),
                    enabled=lambda _item: self.services.tunnel_hostname is not None,
                ),
                pystray.MenuItem(
                    "🔄 Restart web app",
                    lambda: self._enqueue(EVT_RESTART_WEBAPP),
                ),
            ])
        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self._enqueue(EVT_QUIT)),
        ])
        return pystray.Menu(*items)

    # --------------------------------------------------------- event loop

    def _enqueue(self, event: str) -> None:
        self.events.put(event)

    # Public hooks used by the main window when it's opened from the tray,
    # so the two paths share one recorder / hotkey / notification pipeline.
    def request_toggle_record(self) -> None:
        self._hotkeys.request_manual_toggle()
        self._enqueue(EVT_TOGGLE_RECORD)

    def request_quit(self) -> None:
        self._enqueue(EVT_QUIT)

    def get_last_transcription(self) -> Optional[str]:
        return self.last_transcription

    def set_last_transcription(self, text: Optional[str]) -> None:
        self.last_transcription = text

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
            threading.Thread(target=self.services.start_server, daemon=True).start()
        elif event == EVT_STOP_SERVER:
            threading.Thread(target=self.services.stop_server, daemon=True).start()
        elif event == EVT_MODEL_INFO:
            self._show_model_info()
        elif event == EVT_COPY_LOCAL_URL:
            self._copy_local_url()
        elif event == EVT_COPY_TUNNEL_URL:
            self._copy_tunnel_url()
        elif event == EVT_RESTART_WEBAPP:
            threading.Thread(target=self.services.restart_webapp, daemon=True).start()
        elif event == EVT_TOGGLE_APPEND:
            self.set_append_mode(not self.append_mode)
        elif event == EVT_TOGGLE_AUTO_PASTE:
            self.config.auto_paste_after_hotkey = not self.config.auto_paste_after_hotkey
            if self._icon is not None:
                try:
                    self._icon.update_menu()
                except Exception:
                    pass
        elif event == EVT_TOGGLE_SUPPRESS_HOTKEY:
            self.config.suppress_hotkey = not self.config.suppress_hotkey
            self._hotkeys.restart()
            if self._icon is not None:
                try:
                    self._icon.update_menu()
                except Exception:
                    pass
        elif event == EVT_TOGGLE_NOTIFICATIONS:
            self.config.show_notifications = not self.config.show_notifications
            if self._icon is not None:
                try:
                    self._icon.update_menu()
                except Exception:
                    pass
        elif event == EVT_QUIT:
            self.root.quit()

    def set_append_mode(self, enabled: bool) -> None:
        """Flip the append flag and notify any open window to mirror it."""
        if self.append_mode == enabled:
            return
        self.append_mode = bool(enabled)
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass
        for cb in list(self._append_listeners):
            try:
                cb(self.append_mode)
            except Exception:
                pass

    def add_append_listener(self, cb) -> None:
        """Window registers a callback to mirror tray.append_mode changes."""
        if cb not in self._append_listeners:
            self._append_listeners.append(cb)

    def remove_append_listener(self, cb) -> None:
        if cb in self._append_listeners:
            self._append_listeners.remove(cb)

    def _copy_local_url(self) -> None:
        """Copy the loopback URL for use from this PC's browser. Token
        bypasses the auth gate on loopback, but we still tag it so
        pasting into a remote tool (curl from another machine, etc.)
        carries the credential."""
        token = current_auth_token()
        url = append_auth_token(f"https://127.0.0.1:{self.services.webapp.config.port}", token)
        self._copy_with_toast(url)

    def _copy_tunnel_url(self) -> None:
        """Copy the persistent Cloudflare URL — what to bookmark on
        the phone or open from the work PC."""
        if self.services.tunnel_hostname is None:
            self._notify("Cloudflare tunnel", "No webapp/cloudflared.yml — tunnel disabled")
            return
        token = current_auth_token()
        url = append_auth_token(f"https://{self.services.tunnel_hostname}", token)
        self._copy_with_toast(url)

    def _copy_with_toast(self, url: str) -> None:
        try:
            pyperclip.copy(url)
            self._notify("📋 Copied", url)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")

    def _show_model_info(self) -> None:
        """Surface model/memory/runtime info either through the open main
        window's details dialog or, if no window exists, via a toast with
        the one-line summary.
        """
        if self._main_window is not None and self._main_window.root.winfo_exists():
            try:
                self._main_window.show_model_details()
                return
            except Exception as exc:
                logger.debug(f"details dialog failed, falling back to toast: {exc}")
        description = self.services.server.describe()
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

        status = self.services.server.status()
        if not status.running:
            self._notify("Server not running", "Start the whisper-server from the tray menu first.")
            return

        recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            preferred_mics=self.config.resolve_preferred_mics(),
        )
        self._current_recorder = recorder
        self._hotkeys.notify_recording_started()
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
        self._hotkeys.notify_recording_stopped()
        self._recording_popup = None
        self._set_icon_color(recording=False)
        if error is not None:
            self._notify("Recording error", error)
            return
        if recording is None:
            return
        threading.Thread(target=self._transcribe_worker, args=(recording,), daemon=True).start()

    def _transcribe_worker(self, recording: Recording) -> None:
        # Consume the hotkey-initiated flag set on the start press; the
        # worker decides on caret paste based on this single value, so a
        # mid-flight tk-window interaction can't change the outcome.
        from_hotkey = self._hotkeys.consume_record_from_hotkey()

        try:
            webapp_cfg = load_webapp_config()
        except Exception:
            webapp_cfg = None

        status = self.services.server.status()
        if self._transcription_client is None:
            self._transcription_client = build_transcription_client(
                self.config, status.base_url,
            )
        client = self._transcription_client

        result = handle_take(
            recording, self.config, webapp_cfg, client,
            last_transcription=self.last_transcription,
            append_mode=self.append_mode,
            auto_copy=self.config.auto_copy,
            translate=self.translate,
        )

        if result.error is not None:
            self._notify("Transcription failed", result.error)
            return

        if result.silent is not None:
            self._notify(
                "🤫 Empty audio",
                f"Silent take ({result.silent.dbfs:.1f} dBFS) — skipped",
            )
            return

        if result.text is None:
            self._notify("Empty transcription", "The server returned no text.")
            return
        self.last_transcription = result.text

        pasted = False
        if from_hotkey and self.config.auto_paste_after_hotkey:
            pasted = paste_at_caret()

        preview = result.text if len(result.text) <= 80 else result.text[:77] + "…"
        title = "📌 Pasted at caret" if pasted else "📋 Copied to clipboard"
        self._notify(title, preview)

    # -------------------------------------------------------- window / server

    def _open_window(self) -> None:
        if self._main_window is None or not self._main_window.root.winfo_exists():
            # TranscriberApp.__init__ already binds WM_DELETE_WINDOW to its
            # own _on_close (which honours tray_on_close=True by hiding
            # instead of quitting) — no need to re-bind it here.
            self._main_window = TranscriberApp(
                self.config, tray_on_close=True, tray=self, server=self.services.server
            )
        else:
            self._main_window.root.deiconify()
            self._main_window.root.lift()

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
        if not self.config.show_notifications:
            logger.info(f"🔕 (suppressed) {title}: {message}")
            return
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
    """Draw the shared mic glyph (src/mic_glyph.py) as the tray icon.

    Same canonical ``project-scaffolding/brand/mic.svg`` silhouette as the
    static PWA/favicon/Stream Deck family generated by ``gen_app_icons.py``,
    but transparent-background and tinted by recording state.
    """
    return draw_mic(64, pad_ratio=0.15, fg=(*color, 255))


# In-process single-instance guard (project-scaffolding#39): a named mutex held
# by the tray process, NOT a PID file. The former PID-file lock was racy — two
# near-simultaneous starts could both read no-valid-lock and both proceed; the
# named mutex closes that TOCTOU and is freed by the OS on exit (so a crashed
# tray never locks us out). Vendored primitive: app/tray/single_instance.py.
_TRAY_INSTANCE: Optional[SingleInstance] = None


def _acquire_single_instance_lock() -> bool:
    """Return True if we got the lock, False if another tray is already running."""
    global _TRAY_INSTANCE
    _TRAY_INSTANCE = SingleInstance(r"Global\voice-transcriber-tray")
    if not _TRAY_INSTANCE.acquired:
        logger.warning("⚠️  Tray already running; exiting.")
    return _TRAY_INSTANCE.acquired


def _release_single_instance_lock() -> None:
    if _TRAY_INSTANCE is not None:
        _TRAY_INSTANCE.release()


def run_tray(config: AppConfig) -> int:
    if not _acquire_single_instance_lock():
        return 0
    try:
        return TrayApp(config).run()
    finally:
        _release_single_instance_lock()
