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
from typing import TYPE_CHECKING, Any, List, Optional

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
from src.polish import PolishClient, PolishError
from src.polish_prompts import (
    PolishPrompt,
    get_prompt,
    load_polish_prompts,
)
from src.recording_pipeline import SilentTake, finalize_transcript, process_recording
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
        # Multi-prompt polish: load the library at boot. Drop-down selection
        # below mirrors the webapp's "Polish style" picker.
        self.polish_prompts: list = load_polish_prompts()
        self._prompt_label_to_id = {p.label: p.id for p in self.polish_prompts}
        _default_prompt = get_prompt(
            self.webapp_config.polish_prompt_default, self.polish_prompts,
        )

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
        self.polish_model_var = tk.StringVar(value=self.webapp_config.polish_model_default)
        self.polish_style_var = tk.StringVar(value=_default_prompt.label)
        # Append mode mirrors tray.append_mode when this window was opened
        # from the tray; standalone, it's just a local flag.
        initial_append = bool(getattr(self.tray, "append_mode", False)) if self.tray else False
        self.append_var = tk.BooleanVar(value=initial_append)
        self._suppress_append_trace = False
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
        last_frame = ttk.Frame(self.root)
        last_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 6))

        header = ttk.Frame(last_frame)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Last transcription:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        # Order on the right (rightmost first because pack(side=RIGHT) stacks
        # toward the centre): Append | Reset | Copy — mirrors the webapp
        # header's Append | Reset | Incognito grouping.
        self.copy_last_btn = ttk.Button(header, text="📋 Copy", command=self._copy_last, width=10)
        self.copy_last_btn.pack(side=tk.RIGHT)
        self.copy_last_btn.state(["disabled"])
        self.reset_btn = ttk.Button(
            header, text="🧽 Reset", command=self._reset_session, width=10,
        )
        self.reset_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self.reset_btn.state(["disabled"])
        self.append_check = ttk.Checkbutton(
            header, text="➕ Append", variable=self.append_var,
            command=self._on_append_toggle,
        )
        self.append_check.pack(side=tk.RIGHT, padx=(0, 8))
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

        text_wrap = ttk.Frame(last_frame)
        text_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.last_text = tk.Text(text_wrap, wrap=tk.WORD, height=5, font=("Segoe UI", 9),
                                 background="#FAFAFA", relief=tk.FLAT, borderwidth=1)
        scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.last_text.yview)
        # Editable so the user can fix a misheard word before polishing —
        # matches the webapp's transcript box. Edits flow back into the
        # transcript slot via _on_last_text_edited so Polish picks them up.
        self.last_text.configure(yscrollcommand=scroll.set)
        self.last_text.bind("<KeyRelease>", self._on_last_text_edited)
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
            width=18,
            values=tuple(self.webapp_config.polish_models_available),
        )
        self.polish_model_combo.pack(side=tk.LEFT, padx=(8, 4))

        self.polish_style_combo = ttk.Combobox(
            polish_header,
            textvariable=self.polish_style_var,
            state="readonly",
            width=18,
            values=tuple(p.label for p in self.polish_prompts),
        )
        self.polish_style_combo.pack(side=tk.LEFT)

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
        ttk.Button(
            polish_actions, text="⭐ Save defaults", width=15,
            command=self._set_polish_defaults,
        ).pack(side=tk.LEFT)
        ttk.Button(
            polish_actions, text="👁 Show prompt", width=14,
            command=self._show_polish_prompt,
        ).pack(side=tk.LEFT, padx=(6, 0))
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

    def _on_append_toggle(self) -> None:
        """User toggled the checkbox → propagate to tray when owned."""
        if self._suppress_append_trace:
            return
        if self.tray is not None:
            self.tray.set_append_mode(self.append_var.get())

    def _on_tray_append_changed(self, enabled: bool) -> None:
        """Tray menu / hotkey toggle → mirror into the checkbox without
        re-firing the command callback."""
        try:
            self._suppress_append_trace = True
            self.append_var.set(bool(enabled))
        finally:
            self._suppress_append_trace = False

    def _is_append_mode(self) -> bool:
        if self.tray is not None:
            return bool(self.tray.append_mode)
        return bool(self.append_var.get())

    def _set_transcript_buttons(self, enabled: bool) -> None:
        """Enable/disable the Copy last / Reset / Polish trio together.

        The three always move as one — there is a transcript to act on,
        or there isn't — but were previously three hand-written
        `.state([...])` blocks across the refresh/edit/reset call sites,
        already subtly inconsistent about which buttons they touched
        (voice-transcriber#163). Owning the derived state here means a
        future button added to the group only needs to change one place.
        """
        state = ["!disabled"] if enabled else ["disabled"]
        self.copy_last_btn.state(state)
        self.reset_btn.state(state)
        self.polish_btn.state(state)

    def _refresh_last_transcription(self) -> None:
        text = self._current_last_transcription()
        if text == self._displayed_last_transcription:
            return
        self._displayed_last_transcription = text
        self.last_text.delete("1.0", tk.END)
        self._set_transcript_buttons(bool(text))
        if text:
            self.last_text.insert(tk.END, text)
            self.copy_last_btn.config(text="📋 Copy")
        # Source transcript changed → drop any stale polished output.
        self._last_polished = None
        self._render_polished("")
        self.copy_polished_btn.state(["disabled"])

    def _on_last_text_edited(self, _event: object = None) -> None:
        """User typed in the transcript box — push the new content back to
        the source slot (tray-owned or local) so Polish runs against the
        edited text. Mirrors _displayed_last_transcription so the 2 s
        status poll doesn't fight live edits."""
        text = self.last_text.get("1.0", "end-1c") or None
        if self.tray is not None:
            self.tray.last_transcription = text
        else:
            self._last_transcription = text
        self._displayed_last_transcription = text
        self._set_transcript_buttons(bool(text))

    def _reset_session(self) -> None:
        """Clear the transcript + polished panels and the underlying slot
        so the next take starts on a clean page. Equivalent to the
        webapp's 🧽 Reset header button."""
        if self.tray is not None:
            self.tray.last_transcription = None
        else:
            self._last_transcription = None
        self._last_polished = None
        self._displayed_last_transcription = None
        self.last_text.delete("1.0", tk.END)
        self._render_polished("")
        self._set_transcript_buttons(False)
        self.copy_polished_btn.state(["disabled"])

    def _render_polished(self, text: str) -> None:
        self.polished_text.configure(state=tk.NORMAL)
        self.polished_text.delete("1.0", tk.END)
        if text:
            self.polished_text.insert(tk.END, text)
        self.polished_text.configure(state=tk.DISABLED)

    def _run_polish(self) -> None:
        # Sync widget → slot first so a mouse-pasted edit (which doesn't
        # fire <KeyRelease>) still feeds into Polish.
        self._on_last_text_edited()
        text = self._current_last_transcription()
        if not text:
            return
        model = self.polish_model_var.get()
        prompt = self._current_prompt()
        self.polish_btn.state(["disabled"])
        self.polish_btn.config(text="✨ …")
        threading.Thread(
            target=self._polish_worker,
            args=(text, model, prompt.system),
            daemon=True,
        ).start()

    def _polish_worker(self, text: str, model: str, system: str) -> None:
        try:
            result = self.polish_client.polish(text, model=model, system=system)
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
        # Auto-copy polished text + flash the button so the user knows it
        # already landed on the clipboard — matches the webapp's behaviour
        # and saves a manual click on every polish.
        if self.config.auto_copy:
            try:
                pyperclip.copy(result.polished_text)
                self.root.after(0, self._flash_copied_polished)
            except Exception as exc:
                logger.warning(f"⚠️  Auto-copy of polished failed: {exc}")
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
        self._flash_copied_polished()

    def _flash_copied_polished(self) -> None:
        self.copy_polished_btn.config(text="✓ Copied")
        self.root.after(
            1500,
            lambda: self.copy_polished_btn.config(text="📋 Copy polished"),
        )

    def _current_prompt(self) -> PolishPrompt:
        """Resolve the dropdown's label back to a PolishPrompt entry."""
        label = self.polish_style_var.get()
        pid = self._prompt_label_to_id.get(label)
        return get_prompt(pid, self.polish_prompts)

    def _set_polish_defaults(self) -> None:
        model = self.polish_model_var.get()
        if model not in self.webapp_config.polish_models_available:
            messagebox.showwarning(
                "Unknown model",
                f"{model!r} not in webapp_config.polish_models_available.",
            )
            return
        prompt = self._current_prompt()
        try:
            self.webapp_config = update_webapp_config(
                polish_model_default=model,
                polish_prompt_default=prompt.id,
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        messagebox.showinfo(
            "Polish defaults",
            f"Model → {model}\nStyle → {prompt.label}",
        )

    def _show_polish_prompt(self) -> None:
        """Read-only popup with the system prompt about to be sent."""
        prompt = self._current_prompt()
        win = tk.Toplevel(self.root)
        win.title(f"Polish prompt — {prompt.label}")
        win.geometry("640x420")
        win.transient(self.root)
        if prompt.description:
            ttk.Label(
                win, text=prompt.description, wraplength=600,
                font=("Segoe UI", 9, "italic"),
            ).pack(fill=tk.X, padx=12, pady=(12, 4))
        body = tk.Text(
            win, wrap=tk.WORD, font=("Consolas", 10),
            background="#F0F4FA", relief=tk.FLAT, borderwidth=1,
        )
        body.insert(tk.END, prompt.system)
        body.configure(state=tk.DISABLED)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))

    def _copy_last(self) -> None:
        # Sync widget → slot in case the user mouse-pasted edits.
        self._on_last_text_edited()
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
        client = build_transcription_client(self.config, status.base_url)
        translate = bool(self.translate_var.get())
        try:
            result = process_recording(
                recording, self.config, self.webapp_config, client,
                translate=translate,
            )
        except TranscriptionError as e:
            msg = str(e)
            logger.error(f"❌ {msg}")
            self.root.after(0, lambda m=msg: messagebox.showerror("Transcription failed", m))
            return

        if isinstance(result, SilentTake):
            # Silence gate — skip whisper on near-silent takes so it can't
            # hallucinate "Thanks for watching" on an empty recording.
            logger.info(
                f"🤫 Skipping whisper: {result.dbfs:.1f} dBFS < {result.threshold} dBFS"
            )
            self.root.after(
                0,
                lambda d=result.dbfs: messagebox.showinfo(
                    "Empty audio",
                    f"Recording was silent ({d:.1f} dBFS) — nothing transcribed.",
                ),
            )
            return

        self._post_transcription(result)

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
        """Shared post-transcription tail used by both mic and file workers.

        Strip/append-merge/clipboard-copy is delegated to
        ``recording_pipeline.finalize_transcript`` (voice-transcriber#160)
        so the tray and this window can't drift on that ordering again;
        this method owns only the tray-aware slot write and result window.
        """
        finalized = finalize_transcript(
            text,
            last_transcription=self._current_last_transcription(),
            append_mode=self._is_append_mode(),
            auto_copy=self.config.auto_copy,
        )
        if finalized is not None:
            if self.tray is not None:
                self.tray.last_transcription = finalized
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
