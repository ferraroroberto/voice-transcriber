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
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

# Third-party imports
import pyperclip
import yaml
from PIL import Image
import pystray
from pynput import keyboard

try:
    from winotify import Notification as _WinToast  # type: ignore
except ImportError:  # non-Windows or package missing
    _WinToast = None

from src import (
    AppConfig,
    AudioRecorder,
    TranscriptionClient,
    TranscriptionError,
)
from src.inject import parse_simple_hotkey, paste_at_caret
from src.mic_glyph import draw_mic
from src.recorder import Recording
from src.silence import is_silent_samples
from src.webapp_config import append_auth_token, load_webapp_config
from src.whisper_server import OWNERSHIP_OURS, WhisperServerManager
from app.tray.single_instance import SingleInstance
from app.webapp.manager import WebappManager, load_config as load_webapp_runtime_config
from .app import TranscriberApp
from .recording_popup import RecordingPopup

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


def _read_tunnel_hostname(config_path: Path) -> Optional[str]:
    """Pull the first ingress[].hostname out of the cloudflared config.

    Returns None when the file is missing or unparseable — the tray
    treats either case as "no tunnel" and skips spawning cloudflared.
    """
    if not config_path.exists():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"⚠️  Could not parse {config_path}: {exc}")
        return None
    for entry in data.get("ingress") or []:
        if isinstance(entry, dict) and entry.get("hostname"):
            return str(entry["hostname"]).strip()
    return None


class TrayApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.server = WhisperServerManager()
        self.webapp = WebappManager(load_webapp_runtime_config(config.webapp))
        self.events: queue.Queue = queue.Queue()

        # Hidden root so Toplevel popups and after() work.
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self._main_window: Optional[TranscriberApp] = None
        self._recording_popup: Optional[RecordingPopup] = None
        self._current_recorder: Optional[AudioRecorder] = None
        self._icon: Optional[pystray.Icon] = None
        self._hotkey_listener = None  # GlobalHotKeys or Listener depending on hotkey shape
        self._hotkey_target_key = None  # pynput Key when in tap/hold mode
        self._hotkey_key_down: bool = False
        # When set, the in-flight recording was started by a hotkey press —
        # transcription will paste at caret after copy. Cleared by the worker
        # once consumed, or by the tk-button entry point on a fresh start.
        self._record_from_hotkey: bool = False
        # ``time.monotonic()`` of the press that started the current take, while
        # the user is still holding. Used to discriminate tap vs PTT on release.
        self._hotkey_press_started_recording_at: Optional[float] = None
        # ``time.monotonic()`` of the moment the recorder was actually created.
        # Set by ``_toggle_record`` on start, cleared on stop. Used as a second
        # gate on PTT release so a press that races the 80 ms event pump can't
        # stop a take that's barely begun (issue #28).
        self._record_started_monotonic: Optional[float] = None
        self._transcription_client: Optional[TranscriptionClient] = None
        # Model label is queried by pystray on every menu draw; cache so the
        # TCP probe + psutil lookup don't fire on each open.
        self._model_label_cache: tuple[float, str] = (0.0, "🧠 model: ?")
        # Latest non-empty transcription, surfaced in the main window so the
        # user can re-copy it after the clipboard has been overwritten.
        self.last_transcription: Optional[str] = None
        # Append mode: when True, each new take is glued onto the previous
        # transcript with a blank-line separator instead of replacing it.
        # Ephemeral (off on every launch). Mirrored by the tk window's
        # checkbox so toggling either surface stays in sync.
        self.append_mode: bool = False
        self._append_listeners: list = []
        # Cloudflare named tunnel — auto-spawned alongside whisper + uvicorn
        # so a single launch ('tray.bat') brings everything up. Hostname is
        # read from webapp/cloudflared.yml; missing config skips the tunnel.
        self._cloudflared_proc: Optional[subprocess.Popen] = None
        self._tunnel_hostname: Optional[str] = _read_tunnel_hostname(TUNNEL_CONFIG_PATH)

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
        if self.webapp.config.enabled:
            threading.Thread(target=self._start_webapp_worker, daemon=True).start()
        if self._tunnel_hostname is not None:
            threading.Thread(target=self._start_tunnel_worker, daemon=True).start()
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
        # Stop cloudflared first so the public URL goes 5xx immediately
        # while the rest of the cleanup runs.
        self._stop_tunnel()
        try:
            webapp_status = self.webapp.status()
            if webapp_status.ownership == OWNERSHIP_OURS:
                self.webapp.stop()
        except Exception as exc:
            logger.debug(f"webapp shutdown failed: {exc}")
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

        items = [
            pystray.MenuItem(lambda _item: self._cached_model_label(), None, enabled=False),
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
        if self.webapp.config.enabled:
            items.extend([
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda _item: self._cached_webapp_label(), None, enabled=False,
                ),
                pystray.MenuItem(
                    "📋 Copy local URL",
                    lambda: self._enqueue(EVT_COPY_LOCAL_URL),
                ),
                pystray.MenuItem(
                    "📋 Copy Cloudflare URL",
                    lambda: self._enqueue(EVT_COPY_TUNNEL_URL),
                    enabled=lambda _item: self._tunnel_hostname is not None,
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

    _MODEL_LABEL_TTL = 2.0

    def _cached_model_label(self) -> str:
        now = time.monotonic()
        cached_at, label = self._model_label_cache
        if now - cached_at < self._MODEL_LABEL_TTL:
            return label
        try:
            desc = self.server.describe()
            label = f"🧠 {desc.model_display_name}"
        except Exception:
            label = "🧠 model: ?"
        self._model_label_cache = (now, label)
        return label

    def _cached_webapp_label(self) -> str:
        try:
            status = self.webapp.status()
            if status.running:
                return f"🌐 webapp :{status.port}"
            return f"🌐 webapp: down"
        except Exception:
            return "🌐 webapp: ?"

    # ---------------------------------------------------------- hotkey

    def _start_hotkey_listener(self) -> None:
        """Register the global hotkey.

        Single-key hotkeys (``<F8>``) get a low-level keyboard.Listener so we
        can time press↔release and offer push-to-talk alongside tap-toggle.
        Modifier combos fall through to the legacy GlobalHotKeys path —
        toggle-only, since holding a 3-key chord for PTT is awkward.

        ``suppress_hotkey`` is honoured on the simple-key path only; combos
        keep pass-through behaviour to avoid swallowing modifier keystrokes
        from the focused window.
        """
        hotkey = self.config.hotkey
        target_key = parse_simple_hotkey(hotkey)
        if target_key is None:
            try:
                mapping = {hotkey: lambda: self._enqueue_hotkey_toggle(start=None)}
                self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
                self._hotkey_listener.start()
                logger.info(f"🧷 Hotkey {hotkey} (toggle-only — combo)")
            except Exception as e:
                logger.error(f"❌ Failed to register hotkey {hotkey!r}: {e}")
            return

        self._hotkey_target_key = target_key
        suppress = bool(self.config.suppress_hotkey)
        try:
            if suppress:
                # pynput's `suppress=True` flag is all-or-nothing — it eats
                # every key. To suppress only the hotkey, use the per-event
                # `event_filter` hook: ignore non-target keys (return False
                # → no callback, no suppression), and for the target key
                # dispatch press/release manually then raise
                # SuppressException via `suppress_event()` so Windows drops
                # the keystroke before it reaches the focused window.
                target_vk = target_key.value.vk
                # Forward-declare so the closure can call suppress_event()
                # on the listener it's attached to.
                listener_box: list = []
                _WM_KEYDOWNS = (0x0100, 0x0104)  # WM_KEYDOWN, WM_SYSKEYDOWN
                _WM_KEYUPS = (0x0101, 0x0105)    # WM_KEYUP,   WM_SYSKEYUP

                def _filter(msg, data):
                    if data.vkCode != target_vk:
                        return False
                    if msg in _WM_KEYDOWNS:
                        self._on_hotkey_press(target_key)
                    elif msg in _WM_KEYUPS:
                        self._on_hotkey_release(target_key)
                    listener_box[0].suppress_event()  # raises, drops the key

                # NB: pynput strips kwargs that don't start with the platform
                # prefix, so on Windows the filter must be passed as
                # ``win32_event_filter`` — bare ``event_filter`` is silently
                # ignored.
                self._hotkey_listener = keyboard.Listener(
                    on_press=lambda _k: None,
                    on_release=lambda _k: None,
                    win32_event_filter=_filter,
                )
                listener_box.append(self._hotkey_listener)
            else:
                self._hotkey_listener = keyboard.Listener(
                    on_press=self._on_hotkey_press,
                    on_release=self._on_hotkey_release,
                )
            self._hotkey_listener.start()
            logger.info(
                f"🧷 Hotkey {hotkey} (tap = toggle, hold ≥ "
                f"{self.config.ptt_threshold_ms} ms = push-to-talk, "
                f"suppress={'on' if suppress else 'off'})"
            )
        except Exception as e:
            logger.error(f"❌ Failed to register hotkey {hotkey!r}: {e}")

    def _restart_hotkey_listener(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None
        self._hotkey_key_down = False
        self._hotkey_press_started_recording_at = None
        self._start_hotkey_listener()

    def _on_hotkey_press(self, key) -> None:
        if key != self._hotkey_target_key:
            return
        if self._hotkey_key_down:
            return  # auto-repeat from a held key — already handled
        self._hotkey_key_down = True
        if self._current_recorder is None:
            # Press starts a fresh take. Tentatively in PTT mode until release
            # tells us how long the key was held.
            self._hotkey_press_started_recording_at = time.monotonic()
            self._enqueue_hotkey_toggle(start=True)
        elif self._hotkey_press_started_recording_at is None:
            # Already recording in tap-waiting state → this press is the
            # second tap and should stop the take immediately.
            self._enqueue_hotkey_toggle(start=False)
        # else: press while still holding the original — ignore

    # Minimum age of an in-flight recording before a PTT release will stop
    # it. Guards against the press → release happening so fast that the
    # take has barely begun: in that case the user almost certainly meant
    # a tap-toggle (and would lose the take if we stopped it here).
    _MIN_PTT_RECORD_AGE_MS = 400

    def _on_hotkey_release(self, key) -> None:
        if key != self._hotkey_target_key:
            return
        self._hotkey_key_down = False
        started_at = self._hotkey_press_started_recording_at
        if started_at is None:
            return  # release of a second-tap stop, or unrelated release
        held_ms = (time.monotonic() - started_at) * 1000
        self._hotkey_press_started_recording_at = None
        threshold = max(0, int(self.config.ptt_threshold_ms))
        record_started = self._record_started_monotonic
        recorder_age_ms = (
            (time.monotonic() - record_started) * 1000
            if record_started is not None
            else 0.0
        )
        if (
            held_ms >= threshold
            and self._current_recorder is not None
            and recorder_age_ms >= self._MIN_PTT_RECORD_AGE_MS
        ):
            self._enqueue_hotkey_toggle(start=False)
        # else: tap (or PTT release that raced a barely-started recorder).
        # Recording continues; await the second tap.

    def _enqueue_hotkey_toggle(self, start: Optional[bool]) -> None:
        """Enqueue a toggle event from the hotkey path.

        ``start=True``  → mark the upcoming take as hotkey-initiated so the
                          worker pastes at caret on completion.
        ``start=False`` → leave the flag alone; this is the stop press of an
                          already hotkey-initiated take.
        ``start=None``  → combo path (toggle-only); set the flag iff there
                          isn't a recording in flight (i.e. this press is a
                          start).
        """
        if start is True:
            self._record_from_hotkey = True
        elif start is None and self._current_recorder is None:
            self._record_from_hotkey = True
        self._enqueue(EVT_TOGGLE_RECORD)

    # --------------------------------------------------------- event loop

    def _enqueue(self, event: str) -> None:
        self.events.put(event)

    # Public hooks used by the main window when it's opened from the tray,
    # so the two paths share one recorder / hotkey / notification pipeline.
    def request_toggle_record(self) -> None:
        # Tk button: never paste at caret, even when this kicks off a take.
        if self._current_recorder is None:
            self._record_from_hotkey = False
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
        elif event == EVT_COPY_LOCAL_URL:
            self._copy_local_url()
        elif event == EVT_COPY_TUNNEL_URL:
            self._copy_tunnel_url()
        elif event == EVT_RESTART_WEBAPP:
            threading.Thread(target=self._restart_webapp_worker, daemon=True).start()
        elif event == EVT_TOGGLE_APPEND:
            self.set_append_mode(not self.append_mode)
        elif event == EVT_TOGGLE_AUTO_PASTE:
            self.config.auto_paste_after_hotkey = not self.config.auto_paste_after_hotkey
        elif event == EVT_TOGGLE_SUPPRESS_HOTKEY:
            self.config.suppress_hotkey = not self.config.suppress_hotkey
            self._restart_hotkey_listener()
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
        token = self._current_auth_token()
        url = append_auth_token(f"https://127.0.0.1:{self.webapp.config.port}", token)
        self._copy_with_toast(url)

    def _copy_tunnel_url(self) -> None:
        """Copy the persistent Cloudflare URL — what to bookmark on
        the phone or open from the work PC."""
        if self._tunnel_hostname is None:
            self._notify("Cloudflare tunnel", "No webapp/cloudflared.yml — tunnel disabled")
            return
        token = self._current_auth_token()
        url = append_auth_token(f"https://{self._tunnel_hostname}", token)
        self._copy_with_toast(url)

    @staticmethod
    def _current_auth_token() -> str:
        """Re-read the bearer token at copy-time so a freshly rotated
        token lands in the URL without needing a tray restart."""
        try:
            return load_webapp_config().auth_token
        except Exception:
            return ""

    def _copy_with_toast(self, url: str) -> None:
        try:
            pyperclip.copy(url)
            self._notify("📋 Copied", url)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")

    def _restart_webapp_worker(self) -> None:
        try:
            self.webapp.stop()
            self.webapp.start()
            self._notify("Webapp", f"✅ Restarted at {self.webapp.base_url}")
        except RuntimeError as exc:
            self._notify("Webapp restart failed", str(exc))

    def _start_webapp_worker(self) -> None:
        try:
            self.webapp.start()
            logger.info(f"🌐 Webapp ready at {self.webapp.base_url}")
        except RuntimeError as exc:
            logger.warning(f"⚠️  Webapp failed to start: {exc}")
            self._notify("Webapp failed to start", str(exc))

    def _start_tunnel_worker(self) -> None:
        """Spawn cloudflared against webapp/cloudflared.yml so the
        persistent public URL comes up alongside everything else.
        Best-effort — a missing cloudflared binary or a failed launch
        is logged but doesn't take the tray down."""
        bin_path = shutil.which("cloudflared")
        if bin_path is None:
            logger.warning(
                "⚠️  cloudflared not on PATH — public URL won't be reachable. "
                "Install: winget install Cloudflare.cloudflared"
            )
            self._notify(
                "Cloudflare tunnel",
                "cloudflared not on PATH — install via winget",
            )
            return
        cmd = [
            bin_path, "tunnel", "--config", str(TUNNEL_CONFIG_PATH), "run",
        ]
        kw: dict = dict(
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if sys.platform == "win32":
            kw["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        try:
            self._cloudflared_proc = subprocess.Popen(cmd, **kw)
        except OSError as exc:
            logger.warning(f"⚠️  cloudflared failed to launch: {exc}")
            self._notify("Cloudflare tunnel", f"Failed to start: {exc}")
            return
        logger.info(
            f"🌍 Cloudflare tunnel started → https://{self._tunnel_hostname} "
            f"(pid={self._cloudflared_proc.pid})"
        )
        self._persist_tunnel_url()

    def _persist_tunnel_url(self) -> None:
        """Write the public URL to webapp/last_tunnel_url.txt so external
        tooling (the launcher hub) can surface it. Includes ?token=… when
        an auth_token is configured."""
        if self._tunnel_hostname is None:
            return
        url = f"https://{self._tunnel_hostname}"
        try:
            token = (load_webapp_config().auth_token or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"could not read auth_token: {exc}")
            token = ""
        if token:
            url = append_auth_token(url, token)
        try:
            TUNNEL_URL_FILE.parent.mkdir(parents=True, exist_ok=True)
            TUNNEL_URL_FILE.write_text(url + "\n", encoding="utf-8")
            logger.info(f"📡 Tunnel URL → {TUNNEL_URL_FILE}")
        except OSError as exc:
            logger.warning(f"⚠️  Could not write {TUNNEL_URL_FILE}: {exc}")

    def _stop_tunnel(self) -> None:
        proc = self._cloudflared_proc
        if proc is None:
            return
        self._cloudflared_proc = None
        try:
            logger.info(f"🛑 Stopping cloudflared (pid={proc.pid})")
            if sys.platform == "win32":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as exc:
            logger.debug(f"cloudflared stop failed: {exc}")
        try:
            if TUNNEL_URL_FILE.exists():
                TUNNEL_URL_FILE.unlink()
        except OSError:
            pass

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
        self._record_started_monotonic = time.monotonic()
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
        self._record_started_monotonic = None
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
        from_hotkey = self._record_from_hotkey
        self._record_from_hotkey = False

        # Silence gate — skip whisper if the take is below the dBFS
        # threshold so it can't hallucinate on empty audio.
        try:
            threshold = load_webapp_config().silence_dbfs_threshold
        except Exception:
            threshold = -50.0
        silent, dbfs = is_silent_samples(recording.samples, threshold)
        if silent:
            logger.info(
                f"🤫 Skipping whisper: {dbfs:.1f} dBFS < {threshold} dBFS"
            )
            self._notify("🤫 Empty audio", f"Silent take ({dbfs:.1f} dBFS) — skipped")
            return

        status = self.server.status()
        if self._transcription_client is None:
            self._transcription_client = TranscriptionClient(
                status.base_url,
                translate_base_url=self.config.translate_base_url,
            )
        client = self._transcription_client
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

        if self.append_mode and self.last_transcription:
            text = self.last_transcription.rstrip() + "\n\n" + text
        self.last_transcription = text

        if self.config.auto_copy:
            try:
                pyperclip.copy(text)
            except Exception as exc:
                logger.warning(f"⚠️  Clipboard copy failed: {exc}")

        pasted = False
        if from_hotkey and self.config.auto_paste_after_hotkey:
            pasted = paste_at_caret()

        preview = text if len(text) <= 80 else text[:77] + "…"
        title = "📌 Pasted at caret" if pasted else "📋 Copied to clipboard"
        self._notify(title, preview)

    # -------------------------------------------------------- window / server

    def _open_window(self) -> None:
        if self._main_window is None or not self._main_window.root.winfo_exists():
            self._main_window = TranscriberApp(self.config, tray_on_close=True, tray=self, server=self.server)
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

    Same silhouette as the PWA/favicon/Stream Deck icons (gen_app_icons.py),
    just transparent-background and tinted by recording state instead of
    fixed near-white-on-black.
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
