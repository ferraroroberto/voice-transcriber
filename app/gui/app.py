"""Main-window GUI.

Shows server status with a traffic light, start/stop buttons, language
toggle, and a big "Record" button. Recording uses the shared popup. Window
close can optionally hide to tray (caller-controlled flag).

The server status/model line, the last-transcription box, and the polish
flow each live in their own widget class (``ServerPanel``,
``TranscriptPanel``, ``PolishPanel`` — voice-transcriber#177); this class
composes them plus owns window lifecycle, language/mic/gain-boost
settings, and the record/file transcription flow.
"""

from __future__ import annotations

# Standard library imports
import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .tray import TrayApp

# Third-party imports
import pyperclip
from pynput import keyboard
import sounddevice as sd

from src import (
    AppConfig,
    AudioRecorder,
    resolve_iso,
    TranscriptionError,
    build_transcription_client,
)
from src.gain import MAX_GAIN_BOOST_DB, MIN_GAIN_BOOST_DB
from src.recording_pipeline import finalize_transcript, handle_take
from src.webapp_config import load_webapp_config, update_webapp_config
from src.whisper_server import OWNERSHIP_OURS, WhisperServerManager
from .polish_panel import PolishPanel
from .recording_popup import RecordingPopup
from .server_panel import ServerPanel
from .transcript_panel import TranscriptPanel

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
        # tray owns the session, we read/write it via
        # `self.tray.get_last_transcription()` / `.set_last_transcription()`
        # instead and ignore this slot.
        self._last_transcription: Optional[str] = None
        # Tracks what the panel is currently showing so we only redraw on
        # change (the poll runs every 2 s).
        self._displayed_last_transcription: Optional[str] = None
        # Polish — shared config + client with the webapp. Passed into
        # PolishPanel; also used directly by the record/file flow below.
        self.webapp_config = load_webapp_config()

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

        # Language picker — Title-case label backed by ISO under the hood.
        # Old configs spelled it "english"/"spanish"/"italian"; new configs
        # store ISO directly. resolve_iso() handles both shapes.
        _initial_iso = resolve_iso(config.language) or "en"
        _lang_map = config.enabled_language_map()
        self._language_label_to_iso = {label: iso for iso, label in _lang_map.items()}
        self._sorted_language_labels = sorted(_lang_map.values())
        self.language_var = tk.StringVar(
            value=_lang_map.get(_initial_iso, next(iter(_lang_map.values()), "English")),
        )
        # Translate toggle — ephemeral, off on every launch. Shipped here for
        # parity with the webapp's settings panel toggle.
        self.translate_var = tk.BooleanVar(value=False)
        # Force-built-in mic — when on AND the mic combo is at "System
        # default", the heuristic preferred_mics list below biases device
        # selection toward built-in inputs over Bluetooth/headset ones.
        # Default seeded from the shared webapp_config.json.
        self.force_builtin_var = tk.BooleanVar(
            value=self.webapp_config.force_builtin_mic_default,
        )
        # Quiet-environment gain boost — persisted to webapp_config.json
        # immediately on change (shared with the webapp's Settings toggle).
        self.gain_boost_var = tk.BooleanVar(value=self.webapp_config.gain_boost_enabled)
        self.gain_boost_db_var = tk.StringVar(value=str(self.webapp_config.gain_boost_db))

        # Mirror selections into the shared config so tray-initiated recordings
        # (hotkey or tray menu) use whatever the user picked in the window.
        self.language_var.trace_add("write", self._on_language_change)
        # Mirror the translate toggle onto the tray (voice-transcriber#178) —
        # when the tray owns the session, `_toggle_record` delegates straight
        # to `tray.request_toggle_record()` and never reads this window's
        # vars, so the tray needs its own copy to honour the checkbox.
        self.translate_var.trace_add("write", self._on_translate_change)

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
        self.mic_var.trace_add("write", self._apply_mic_selection)
        self.force_builtin_var.trace_add("write", self._apply_mic_selection)
        self.gain_boost_var.trace_add("write", self._save_gain_boost_settings)
        self.gain_boost_db_var.trace_add("write", self._save_gain_boost_settings)

        self._build_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_status()

    # ------------------------------------------------------------ layout

    def _on_language_change(self, *_: object) -> None:
        label = self.language_var.get()
        iso = self._language_label_to_iso.get(label)
        if iso:
            # Store the ISO so tray-initiated recordings (hotkey path) pick
            # up whatever the user picked here. Old code stored "english"
            # etc., AppConfig.whisper_language now resolves either form.
            self.config.language = iso

    def _on_translate_change(self, *_: object) -> None:
        if self.tray is not None:
            self.tray.translate = bool(self.translate_var.get())

    def _apply_mic_selection(self, *_: object) -> None:
        """Combine the mic combo + force-built-in checkbox into a single
        preferred_mics list. An explicit combo pick always wins; otherwise
        the checkbox switches between OS-default and a built-in heuristic
        (substring matches that bias selection away from BT/headsets)."""
        label = self.mic_var.get()
        if label != "System default":
            self.config.preferred_mics = [label]
        elif self.force_builtin_var.get():
            self.config.preferred_mics = ["realtek", "built-in", "internal"]
        else:
            self.config.preferred_mics = None

    def _save_gain_boost_settings(self, *_: object) -> None:
        """Persist the gain-boost toggle/value to webapp_config.json as
        soon as either changes — mirrors the webapp's immediate-toggle
        settings, no explicit Save button needed for this one pair."""
        try:
            db = float(self.gain_boost_db_var.get())
        except ValueError:
            return
        try:
            self.webapp_config = update_webapp_config(
                gain_boost_enabled=self.gain_boost_var.get(),
                gain_boost_db=db,
            )
        except (ValueError, OSError) as exc:
            logger.warning(f"⚠️  Could not save gain boost settings: {exc}")

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 6}

        title = ttk.Label(self.root, text="Voice Transcription", font=("Segoe UI", 14, "bold"))
        title.pack(pady=(14, 4))

        self.server_panel = ServerPanel(self.root, self.server)
        self.server_panel.pack(fill=tk.X)

        # Dictation mode — full Whisper language list (alphabetical), with a
        # 🌐 Translate-to-English toggle alongside.
        lang_frame = ttk.Frame(self.root)
        lang_frame.pack(fill=tk.X, **pad)
        ttk.Label(lang_frame, text="Language:").pack(side=tk.LEFT)
        lang_combo = ttk.Combobox(
            lang_frame, textvariable=self.language_var, state="readonly", width=22,
            values=tuple(self._sorted_language_labels),
        )
        lang_combo.pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(
            lang_frame, text="🌐 Translate to English",
            variable=self.translate_var,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Mic selector
        mic_frame = ttk.Frame(self.root)
        mic_frame.pack(fill=tk.X, **pad)
        ttk.Label(mic_frame, text="Mic:").pack(side=tk.LEFT)
        mic_combo = ttk.Combobox(
            mic_frame, textvariable=self.mic_var, state="readonly", width=28,
            values=["System default"] + self._mic_devices,
        )
        mic_combo.pack(side=tk.LEFT, padx=8)

        # Force-built-in toggle — only effective when mic combo is at
        # "System default"; biases preferred_mics toward built-in inputs.
        force_builtin_frame = ttk.Frame(self.root)
        force_builtin_frame.pack(fill=tk.X, padx=16, pady=(0, 6))
        ttk.Checkbutton(
            force_builtin_frame,
            text="Force built-in mic (skip Bluetooth)",
            variable=self.force_builtin_var,
        ).pack(side=tk.LEFT)

        # Quiet-environment gain boost — amplifies captured audio before
        # whisper, distinct from the silence-skip anti-hallucination gate.
        gain_boost_frame = ttk.Frame(self.root)
        gain_boost_frame.pack(fill=tk.X, padx=16, pady=(0, 6))
        ttk.Checkbutton(
            gain_boost_frame,
            text="🔊 Quiet-environment gain boost",
            variable=self.gain_boost_var,
        ).pack(side=tk.LEFT)
        ttk.Spinbox(
            gain_boost_frame,
            from_=MIN_GAIN_BOOST_DB, to=MAX_GAIN_BOOST_DB, increment=1,
            textvariable=self.gain_boost_db_var, width=4,
        ).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(gain_boost_frame, text="dB").pack(side=tk.LEFT)

        # Primary actions
        record_btn = ttk.Button(self.root, text="🎤 Record / Stop", command=self._toggle_record)
        record_btn.pack(fill=tk.X, **pad)
        record_btn.configure(padding=(0, 10))

        file_btn = ttk.Button(self.root, text="📁 Transcribe file…", command=self._transcribe_file_dialog)
        file_btn.pack(fill=tk.X, **pad)

        # Last-transcription panel — survives clipboard overwrites so the user
        # can always re-copy whatever they dictated last, until the app exits.
        # Append mode mirrors tray.append_mode when this window was opened
        # from the tray; standalone, it's just a local flag.
        initial_append = bool(getattr(self.tray, "append_mode", False)) if self.tray else False
        self.transcript_panel = TranscriptPanel(
            self.root,
            initial_append=initial_append,
            on_text_changed=self._on_transcript_text_changed,
            on_reset_requested=self._reset_session,
            on_append_toggle=self._on_append_toggle,
        )
        self.transcript_panel.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 6))
        # When tray-owned, mirror tray.append_mode → checkbox.
        if self.tray is not None:
            self.tray.add_append_listener(self._on_tray_append_changed)
            self.root.bind(
                "<Destroy>",
                lambda e, cb=self._on_tray_append_changed: (
                    self.tray.remove_append_listener(cb)
                    if e.widget is self.root else None
                ),
                add="+",
            )

        # Polish row — same flow as the webapp, shares webapp_config.json.
        self.polish_panel = PolishPanel(
            self.root,
            webapp_config=self.webapp_config,
            config=self.config,
            get_transcript_text=self.transcript_panel.get_text,
        )
        self.polish_panel.pack(fill=tk.X, padx=16, pady=(8, 4))

        quit_btn = ttk.Button(self.root, text="Quit", command=self._quit)
        quit_btn.pack(fill=tk.X, **pad)

    # --------------------------------------------------- server status polling

    def _poll_status(self) -> None:
        self.server_panel.refresh()
        self._refresh_last_transcription()
        self.root.after(POLL_MS, self._poll_status)

    def show_model_details(self) -> None:
        """Public passthrough for the tray's "Model info" menu item, which
        prefers this window's dialog when one is open (voice-transcriber#177
        — was a private ``_show_model_details()`` reach-through)."""
        self.server_panel.show_model_details()

    def _current_last_transcription(self) -> Optional[str]:
        """Source of truth for the panel: the tray when one owns the session,
        otherwise this window's own slot.
        """
        if self.tray is not None:
            return self.tray.get_last_transcription()
        return self._last_transcription

    def _on_append_toggle(self, enabled: bool) -> None:
        """User toggled the checkbox → propagate to tray when owned."""
        if self.tray is not None:
            self.tray.set_append_mode(enabled)

    def _on_tray_append_changed(self, enabled: bool) -> None:
        """Tray menu / hotkey toggle → mirror into the checkbox without
        re-firing the command callback."""
        self.transcript_panel.set_append_checked(enabled)

    def _is_append_mode(self) -> bool:
        if self.tray is not None:
            return bool(self.tray.append_mode)
        return bool(self.transcript_panel.append_var.get())

    def _refresh_last_transcription(self) -> None:
        text = self._current_last_transcription()
        if text == self._displayed_last_transcription:
            return
        self._displayed_last_transcription = text
        self.transcript_panel.set_text(text)
        # Source transcript changed → drop any stale polished output.
        self.polish_panel.clear()

    def _on_transcript_text_changed(self, text: str) -> None:
        """User typed in the transcript box — push the new content back to
        the source slot (tray-owned or local) so the append-merge on the
        next take, and Polish (which reads the panel directly), pick up
        the edit. Mirrors _displayed_last_transcription so the 2 s status
        poll doesn't fight live edits."""
        text = text or None
        if self.tray is not None:
            self.tray.set_last_transcription(text)
        else:
            self._last_transcription = text
        self._displayed_last_transcription = text
        self.polish_panel.set_enabled(bool(text))

    def _reset_session(self) -> None:
        """Clear the transcript + polished panels and the underlying slot
        so the next take starts on a clean page. Equivalent to the
        webapp's 🧽 Reset header button."""
        if self.tray is not None:
            self.tray.set_last_transcription(None)
        else:
            self._last_transcription = None
        self._displayed_last_transcription = None
        self.transcript_panel.clear()
        self.polish_panel.clear()

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
        client = build_transcription_client(self.config, status.base_url)
        translate = bool(self.translate_var.get())

        result = handle_take(
            recording, self.config, self.webapp_config, client,
            last_transcription=self._current_last_transcription(),
            append_mode=self._is_append_mode(),
            auto_copy=self.config.auto_copy,
            translate=translate,
        )

        if result.error is not None:
            self.root.after(
                0, lambda m=result.error: messagebox.showerror("Transcription failed", m)
            )
            return

        if result.silent is not None:
            # Silence gate — skip whisper on near-silent takes so it can't
            # hallucinate "Thanks for watching" on an empty recording.
            dbfs = result.silent.dbfs
            self.root.after(
                0,
                lambda d=dbfs: messagebox.showinfo(
                    "Empty audio",
                    f"Recording was silent ({d:.1f} dBFS) — nothing transcribed.",
                ),
            )
            return

        self._show_finalized(result.text)

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

    def _post_transcription(self, text: str) -> None:
        """File-flow post-transcription tail: finalize raw whisper text,
        then hand off to ``_show_finalized``.

        The mic flow doesn't call this — ``handle_take`` already ran
        ``finalize_transcript`` (voice-transcriber#174), so it goes straight
        to ``_show_finalized`` with the already-finalized text instead.
        """
        finalized = finalize_transcript(
            text,
            last_transcription=self._current_last_transcription(),
            append_mode=self._is_append_mode(),
            auto_copy=self.config.auto_copy,
        )
        self._show_finalized(finalized)

    def _show_finalized(self, finalized: Optional[str]) -> None:
        """Write already-finalized text into the tray-aware last-
        transcription slot and show the result window. Shared tail for
        both the mic flow (via ``handle_take``) and the file flow (via
        ``_post_transcription``)."""
        if finalized is not None:
            if self.tray is not None:
                self.tray.set_last_transcription(finalized)
            else:
                self._last_transcription = finalized
        self.root.after(0, lambda t=(finalized or ""): self._show_result(t))

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
        client = build_transcription_client(self.config, status.base_url)
        iso_lang = self.config.whisper_language
        translate = bool(self.translate_var.get())
        try:
            text = client.transcribe_file(
                path,
                language=iso_lang,
                translate=translate,
            )
        except TranscriptionError as e:
            msg = str(e)
            logger.error(f"❌ {msg}")
            self.root.after(0, lambda m=msg: messagebox.showerror("Transcription failed", m))
            return
        self._post_transcription(text)

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
        """Global F8 (configurable) — toggles the same recorder
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
