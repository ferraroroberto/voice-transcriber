"""Main-window GUI.

Shows server status with a traffic light, start/stop buttons, language
toggle, and a big "Record" button. Recording uses the shared popup. Window
close can optionally hide to tray (caller-controlled flag).
"""

from __future__ import annotations

# Standard library imports
import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .tray import TrayApp

# Third-party imports
import pyperclip
from pynput import keyboard
import sounddevice as sd

from src import (
    AppConfig,
    AudioRecorder,
    LANGUAGE_MODE_LABELS,
    LANGUAGE_MODES,
    TranscriptionClient,
    TranscriptionError,
)
from src.polish import PolishClient, PolishError
from src.webapp_config import load_webapp_config, update_webapp_config
from src.whisper_server import OWNERSHIP_OURS, WhisperServerManager
from .diagnostics_window import DiagnosticsWindow
from .recording_popup import RecordingPopup

logger = logging.getLogger(__name__)

POLL_MS = 2000


class TranscriberApp:
    def __init__(
        self,
        config: AppConfig,
        tray_on_close: bool = False,
        tray: Optional["TrayApp"] = None,
        server: Optional[WhisperServerManager] = None,
    ) -> None:
        self.config = config
        self.tray_on_close = tray_on_close
        self.tray = tray
        self.server = server if server is not None else WhisperServerManager()
        self._current_recorder: Optional[AudioRecorder] = None
        self._hotkey_listener: Optional[keyboard.GlobalHotKeys] = None
        # Standalone-mode storage for the most recent transcription. When a
        # tray owns the session, we read from `self.tray.last_transcription`
        # instead and ignore this slot.
        self._last_transcription: Optional[str] = None
        # Tracks what the panel is currently showing so we only redraw on
        # change (the poll runs every 2 s).
        self._displayed_last_transcription: Optional[str] = None
        # Polish — shared config + client with the webapp.
        self.webapp_config = load_webapp_config()
        self.polish_client = PolishClient(self.webapp_config.llm_hub_url)
        self._last_polished: Optional[str] = None

        # When launched from the tray, live as a Toplevel of the tray's root so
        # both share one tk interpreter — and delegate record/quit/toasts back
        # to the tray so we don't run a parallel recorder/hotkey/notification
        # path that ignores the tray icon color, global hotkey, and toasts.
        if tray is not None:
            self.root = tk.Toplevel(tray.root)
        else:
            self.root = tk.Tk()
        self.root.title("Voice Transcription")
        self.root.geometry("420x560")
        self.root.minsize(420, 480)
        self.root.configure(background="#FFFFFF")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background="#FFFFFF", foreground="#000000")
        style.configure("TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#FFFFFF", foreground="#000000")
        style.configure("TCheckbutton", background="#FFFFFF", foreground="#000000")
        style.map("TCheckbutton", background=[("active", "#FFFFFF")])

        self.status_var = tk.StringVar(value="checking…")
        self.model_var = tk.StringVar(value="model: …")
        self.language_var = tk.StringVar(value=LANGUAGE_MODE_LABELS[config.language])
        self.polish_model_var = tk.StringVar(value=self.webapp_config.polish_model_default)

        # Mirror selections into the shared config so tray-initiated recordings
        # (hotkey or tray menu) use whatever the user picked in the window.
        self.language_var.trace_add("write", self._on_language_change)

        # Enumerate input devices once; used by the mic combobox.
        try:
            self._mic_devices: List[str] = [
                d["name"]
                for d in sd.query_devices()
                if d["max_input_channels"] > 0
            ]
        except Exception:
            self._mic_devices = []

        _initial_mic = "System default"
        if config.preferred_mics:
            pref = config.preferred_mics[0].lower()
            for name in self._mic_devices:
                if pref in name.lower() or name.lower() in pref:
                    _initial_mic = name
                    break
        self.mic_var = tk.StringVar(value=_initial_mic)
        self.mic_var.trace_add("write", self._on_mic_change)

        self._build_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_status()

    # ------------------------------------------------------------ layout

    def _on_language_change(self, *_: object) -> None:
        label = self.language_var.get()
        for mode, mode_label in LANGUAGE_MODE_LABELS.items():
            if mode_label == label:
                self.config.language = mode
                return

    def _on_mic_change(self, *_: object) -> None:
        label = self.mic_var.get()
        if label == "System default":
            self.config.preferred_mics = None
        else:
            self.config.preferred_mics = [label]

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 6}

        title = ttk.Label(self.root, text="Voice Transcription", font=("Segoe UI", 14, "bold"))
        title.pack(pady=(14, 4))

        # Server status row
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, **pad)
        ttk.Label(status_frame, text="Server:").pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=8)

        # Model identity — users want to confirm at a glance *which* whisper
        # build is actually serving (e.g. large-v3-turbo, size on disk, RSS).
        model_frame = ttk.Frame(self.root)
        model_frame.pack(fill=tk.X, **pad)
        ttk.Label(model_frame, textvariable=self.model_var).pack(side=tk.LEFT)
        ttk.Button(model_frame, text="🩺 Diagnostics", command=self._show_diagnostics, width=14).pack(side=tk.RIGHT)
        ttk.Button(model_frame, text="ℹ Details", command=self._show_model_details, width=10).pack(side=tk.RIGHT, padx=(0, 4))

        server_btn_frame = ttk.Frame(self.root)
        server_btn_frame.pack(fill=tk.X, **pad)
        self.start_btn = ttk.Button(server_btn_frame, text="▶ Start server", command=self._start_server)
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        self.stop_btn = ttk.Button(server_btn_frame, text="■ Stop server", command=self._stop_server)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        # Dictation mode
        lang_frame = ttk.Frame(self.root)
        lang_frame.pack(fill=tk.X, **pad)
        ttk.Label(lang_frame, text="Language:").pack(side=tk.LEFT)
        lang_combo = ttk.Combobox(
            lang_frame, textvariable=self.language_var, state="readonly", width=22,
            values=tuple(LANGUAGE_MODE_LABELS[m] for m in LANGUAGE_MODES),
        )
        lang_combo.pack(side=tk.LEFT, padx=8)

        # Mic selector
        mic_frame = ttk.Frame(self.root)
        mic_frame.pack(fill=tk.X, **pad)
        ttk.Label(mic_frame, text="Mic:").pack(side=tk.LEFT)
        mic_combo = ttk.Combobox(
            mic_frame, textvariable=self.mic_var, state="readonly", width=28,
            values=["System default"] + self._mic_devices,
        )
        mic_combo.pack(side=tk.LEFT, padx=8)

        # Primary actions
        record_btn = ttk.Button(self.root, text="🎤 Record / Stop", command=self._toggle_record)
        record_btn.pack(fill=tk.X, **pad)
        record_btn.configure(padding=(0, 10))

        file_btn = ttk.Button(self.root, text="📁 Transcribe file…", command=self._transcribe_file_dialog)
        file_btn.pack(fill=tk.X, **pad)

        # Last-transcription panel — survives clipboard overwrites so the user
        # can always re-copy whatever they dictated last, until the app exits.
        last_frame = ttk.Frame(self.root)
        last_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 6))

        header = ttk.Frame(last_frame)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Last transcription:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.copy_last_btn = ttk.Button(header, text="📋 Copy", command=self._copy_last, width=10)
        self.copy_last_btn.pack(side=tk.RIGHT)
        self.copy_last_btn.state(["disabled"])

        text_wrap = ttk.Frame(last_frame)
        text_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.last_text = tk.Text(text_wrap, wrap=tk.WORD, height=5, font=("Segoe UI", 9),
                                 background="#FAFAFA", relief=tk.FLAT, borderwidth=1)
        scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.last_text.yview)
        self.last_text.configure(yscrollcommand=scroll.set, state=tk.DISABLED)
        self.last_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Polish row — same flow as the webapp, shares webapp_config.json.
        polish_frame = ttk.Frame(self.root)
        polish_frame.pack(fill=tk.X, padx=16, pady=(8, 4))

        polish_header = ttk.Frame(polish_frame)
        polish_header.pack(fill=tk.X)
        ttk.Label(polish_header, text="✨ Polish:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        self.polish_model_combo = ttk.Combobox(
            polish_header,
            textvariable=self.polish_model_var,
            state="readonly",
            width=22,
            values=tuple(self.webapp_config.polish_models_available),
        )
        self.polish_model_combo.pack(side=tk.LEFT, padx=8)

        ttk.Button(
            polish_header, text="⭐ Default", width=10,
            command=self._set_default_polish_model,
        ).pack(side=tk.LEFT)

        self.polish_btn = ttk.Button(
            polish_header, text="✨ Polish", width=10,
            command=self._run_polish,
        )
        self.polish_btn.pack(side=tk.RIGHT)
        self.polish_btn.state(["disabled"])

        polish_text_wrap = ttk.Frame(polish_frame)
        polish_text_wrap.pack(fill=tk.X, pady=(4, 0))
        self.polished_text = tk.Text(
            polish_text_wrap, wrap=tk.WORD, height=4, font=("Segoe UI", 9),
            background="#F0F4FA", relief=tk.FLAT, borderwidth=1,
        )
        polish_scroll = ttk.Scrollbar(polish_text_wrap, orient=tk.VERTICAL, command=self.polished_text.yview)
        self.polished_text.configure(yscrollcommand=polish_scroll.set, state=tk.DISABLED)
        self.polished_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        polish_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        polish_actions = ttk.Frame(polish_frame)
        polish_actions.pack(fill=tk.X, pady=(4, 0))
        self.copy_polished_btn = ttk.Button(
            polish_actions, text="📋 Copy polished", command=self._copy_polished,
        )
        self.copy_polished_btn.pack(side=tk.RIGHT)
        self.copy_polished_btn.state(["disabled"])

        quit_btn = ttk.Button(self.root, text="Quit", command=self._quit)
        quit_btn.pack(fill=tk.X, **pad)

    # --------------------------------------------------- server status polling

    def _poll_status(self) -> None:
        status = self.server.status()
        if status.running and status.ownership == OWNERSHIP_OURS:
            self.status_var.set(f"🟢 running (ours) :{status.port}")
        elif status.running:
            self.status_var.set(f"🟢 running (external) :{status.port}")
        else:
            self.status_var.set(f"🔴 not running :{status.port}")

        description = self.server.describe(status=status)
        self.model_var.set(f"🧠 {description.summary_line()}")

        self.start_btn.state(["disabled"] if status.running else ["!disabled"])
        self.stop_btn.state(["!disabled"] if status.running and status.ownership == OWNERSHIP_OURS else ["disabled"])

        self._refresh_last_transcription()

        self.root.after(POLL_MS, self._poll_status)

    def _current_last_transcription(self) -> Optional[str]:
        """Source of truth for the panel: the tray when one owns the session,
        otherwise this window's own slot.
        """
        if self.tray is not None:
            return self.tray.last_transcription
        return self._last_transcription

    def _refresh_last_transcription(self) -> None:
        text = self._current_last_transcription()
        if text == self._displayed_last_transcription:
            return
        self._displayed_last_transcription = text
        self.last_text.configure(state=tk.NORMAL)
        self.last_text.delete("1.0", tk.END)
        if text:
            self.last_text.insert(tk.END, text)
            self.copy_last_btn.state(["!disabled"])
            self.copy_last_btn.config(text="📋 Copy")
            self.polish_btn.state(["!disabled"])
        else:
            self.copy_last_btn.state(["disabled"])
            self.polish_btn.state(["disabled"])
        self.last_text.configure(state=tk.DISABLED)
        # Source transcript changed → drop any stale polished output.
        self._last_polished = None
        self._render_polished("")
        self.copy_polished_btn.state(["disabled"])

    def _render_polished(self, text: str) -> None:
        self.polished_text.configure(state=tk.NORMAL)
        self.polished_text.delete("1.0", tk.END)
        if text:
            self.polished_text.insert(tk.END, text)
        self.polished_text.configure(state=tk.DISABLED)

    def _run_polish(self) -> None:
        text = self._current_last_transcription()
        if not text:
            return
        model = self.polish_model_var.get()
        self.polish_btn.state(["disabled"])
        self.polish_btn.config(text="✨ …")
        threading.Thread(
            target=self._polish_worker, args=(text, model), daemon=True,
        ).start()

    def _polish_worker(self, text: str, model: str) -> None:
        try:
            result = self.polish_client.polish(text, model=model)
        except PolishError as exc:
            msg = str(exc)
            logger.error(f"❌ polish: {msg}")
            self.root.after(
                0,
                lambda m=msg: messagebox.showerror("Polish failed", m),
            )
            self.root.after(0, self._reset_polish_button)
            return
        self._last_polished = result.polished_text
        self.root.after(0, lambda: self._render_polished(result.polished_text))
        self.root.after(0, lambda: self.copy_polished_btn.state(["!disabled"]))
        self.root.after(0, self._reset_polish_button)

    def _reset_polish_button(self) -> None:
        self.polish_btn.state(["!disabled"])
        self.polish_btn.config(text="✨ Polish")

    def _copy_polished(self) -> None:
        if not self._last_polished:
            return
        try:
            pyperclip.copy(self._last_polished)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")
            return
        self.copy_polished_btn.config(text="✓ Copied")
        self.root.after(
            1500,
            lambda: self.copy_polished_btn.config(text="📋 Copy polished"),
        )

    def _set_default_polish_model(self) -> None:
        model = self.polish_model_var.get()
        if model not in self.webapp_config.polish_models_available:
            messagebox.showwarning(
                "Unknown model",
                f"{model!r} not in webapp_config.polish_models_available.",
            )
            return
        try:
            self.webapp_config = update_webapp_config(polish_model_default=model)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        messagebox.showinfo("Polish default", f"Default → {model}")

    def _copy_last(self) -> None:
        text = self._current_last_transcription()
        if not text:
            return
        try:
            pyperclip.copy(text)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")
            return
        self.copy_last_btn.config(text="✓ Copied")
        self.root.after(1500, lambda: self.copy_last_btn.config(text="📋 Copy"))

    def _show_diagnostics(self) -> None:
        DiagnosticsWindow(self.root, self.server)

    def _show_model_details(self) -> None:
        description = self.server.describe()
        win = tk.Toplevel(self.root)
        win.title("Whisper model details")
        win.geometry("560x360")
        win.transient(self.root)
        text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
        text.insert(tk.END, "\n".join(description.multiline()))
        if not description.runtime_info:
            text.insert(
                tk.END,
                "\n\n(no runtime info yet — start the server or run a transcription"
                " to populate whisper.cpp's startup diagnostics)",
            )
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

    def _start_server(self) -> None:
        self.status_var.set("⏳ starting…")
        threading.Thread(target=self._start_server_worker, daemon=True).start()

    def _start_server_worker(self) -> None:
        try:
            self.server.start()
        except RuntimeError as e:
            msg = str(e)
            logger.error(msg)
            self.root.after(0, lambda m=msg: messagebox.showerror("Server failed to start", m))

    def _stop_server(self) -> None:
        threading.Thread(target=self.server.stop, daemon=True).start()

    # ---------------------------------------------------------- record flow

    def _toggle_record(self) -> None:
        # When owned by the tray, delegate: the tray already manages the
        # recorder, global hotkey, icon color, and result toast.
        if self.tray is not None:
            self.tray.request_toggle_record()
            return

        # Second press → stop the in-flight recording.
        if self._current_recorder is not None:
            self._current_recorder.request_stop()
            return

        status = self.server.status()
        if not status.running:
            messagebox.showwarning(
                "Server not running",
                "Start the whisper-server first (▶ Start server).",
            )
            return

        recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            preferred_mics=self.config.resolve_preferred_mics(),
        )
        self._current_recorder = recorder
        RecordingPopup(
            parent=self.root,
            recorder=recorder,
            max_seconds=self.config.max_record_seconds,
            on_done=self._on_record_done,
            hotkey_label=self.config.hotkey_label,
        )

    def _on_record_done(self, recording, error) -> None:
        self._current_recorder = None
        if error is not None:
            messagebox.showerror("Recording error", error)
            return
        if recording is None:
            return
        threading.Thread(
            target=self._transcribe_and_show,
            args=(recording,),
            daemon=True,
        ).start()

    def _transcribe_and_show(self, recording) -> None:
        status = self.server.status()
        client = TranscriptionClient(status.base_url)
        iso_lang = self.config.whisper_language
        try:
            text = client.transcribe_array(
                recording.samples, recording.sample_rate,
                language=iso_lang,
            )
        except TranscriptionError as e:
            msg = str(e)
            logger.error(f"❌ {msg}")
            self.root.after(0, lambda m=msg: messagebox.showerror("Transcription failed", m))
            return

        text = text.strip()
        if text:
            self._last_transcription = text

        if self.config.auto_copy:
            try:
                pyperclip.copy(text)
            except Exception as exc:
                logger.warning(f"⚠️  Clipboard copy failed: {exc}")

        self.root.after(0, lambda: self._show_result(text))

    def _show_result(self, text: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("Transcription")
        win.geometry("640x360")
        win.transient(self.root)

        text_widget = tk.Text(win, wrap=tk.WORD)
        text_widget.insert(tk.END, text)
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))

        def copy() -> None:
            pyperclip.copy(text)
            copy_btn.config(text="✓ Copied")

        copy_btn = ttk.Button(btns, text="Copy", command=copy)
        copy_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(btns, text="Close", command=win.destroy).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

    # ---------------------------------------------------------- file flow

    def _transcribe_file_dialog(self) -> None:
        status = self.server.status()
        if not status.running:
            messagebox.showwarning("Server not running", "Start the whisper-server first.")
            return
        path = filedialog.askopenfilename(
            title="Select audio file",
            filetypes=(
                ("Audio", "*.mp3 *.wav *.m4a *.flac *.ogg *.mp4 *.webm"),
                ("All files", "*.*"),
            ),
        )
        if not path:
            return
        threading.Thread(target=self._transcribe_file_worker, args=(path,), daemon=True).start()

    def _transcribe_file_worker(self, path: str) -> None:
        status = self.server.status()
        client = TranscriptionClient(status.base_url)
        iso_lang = self.config.whisper_language
        try:
            text = client.transcribe_file(
                path,
                language=iso_lang,
            )
        except TranscriptionError as e:
            msg = str(e)
            self.root.after(0, lambda m=msg: messagebox.showerror("Transcription failed", m))
            return
        text = text.strip()
        if text:
            self._last_transcription = text
        if self.config.auto_copy:
            try:
                pyperclip.copy(text)
            except Exception:
                pass
        self.root.after(0, lambda: self._show_result(text))

    # ------------------------------------------------------------ lifecycle

    def _on_close(self) -> None:
        if self.tray_on_close:
            self.root.withdraw()
            return
        self._quit()

    def _quit(self) -> None:
        # When owned by the tray, route Quit through the tray so it handles
        # the full shutdown (hotkey teardown, server stop + toast, icon stop).
        if self.tray is not None:
            self.tray.request_quit()
            return
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None
        status = self.server.status()
        if status.running and status.ownership == OWNERSHIP_OURS:
            self.server.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _start_hotkey_listener(self) -> None:
        """Global Ctrl+Alt+Space (configurable) — toggles the same recorder
        as the Record button. Only registered in standalone gui mode; when
        the main window is opened from the tray, the tray owns the hotkey.
        """
        hotkey = self.config.hotkey
        try:
            mapping = {hotkey: lambda: self.root.after(0, self._toggle_record)}
            self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
            self._hotkey_listener.start()
        except Exception as e:
            logger.error(f"❌ Failed to register global hotkey {hotkey!r}: {e}")

    def run(self) -> int:
        if not self.tray_on_close:
            self._start_hotkey_listener()
        self.root.mainloop()
        return 0


def run_gui(config: AppConfig, tray_on_close: bool = False) -> int:
    app = TranscriberApp(config, tray_on_close=tray_on_close)
    return app.run()
