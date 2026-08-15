"""Last-transcription panel — text box plus Copy / Reset / Append.

Extracted from ``TranscriberApp`` (voice-transcriber#177). Owns its own
widgets and the view-logic that follows directly from them (buttons
track "is there text", the append checkbox tracks its own var). Anything
that crosses into tray/session ownership or the Polish panel — where the
transcript actually lives, whether a reset should also clear polish — is
a callback into the owner, which still owns that coordination.
"""

from __future__ import annotations

# Standard library imports
import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

# Third-party imports
import pyperclip

logger = logging.getLogger(__name__)


class TranscriptPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_append: bool,
        on_text_changed: Callable[[str], None],
        on_reset_requested: Callable[[], None],
        on_append_toggle: Callable[[bool], None],
    ) -> None:
        super().__init__(parent)
        self._on_text_changed = on_text_changed
        self._on_reset_requested = on_reset_requested
        self._on_append_toggle = on_append_toggle
        self.append_var = tk.BooleanVar(value=initial_append)
        self._suppress_append_trace = False
        self._build_widgets()

    def _build_widgets(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Last transcription:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        # Order on the right (rightmost first because pack(side=RIGHT) stacks
        # toward the centre): Append | Reset | Copy — mirrors the webapp
        # header's Append | Reset | Incognito grouping.
        self.copy_last_btn = ttk.Button(header, text="📋 Copy", command=self._copy_last, width=10)
        self.copy_last_btn.pack(side=tk.RIGHT)
        self.copy_last_btn.state(["disabled"])
        self.reset_btn = ttk.Button(
            header, text="🧽 Reset", command=self._on_reset_requested, width=10,
        )
        self.reset_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self.reset_btn.state(["disabled"])
        self.append_check = ttk.Checkbutton(
            header, text="➕ Append", variable=self.append_var,
            command=self._handle_append_toggle,
        )
        self.append_check.pack(side=tk.RIGHT, padx=(0, 8))

        text_wrap = ttk.Frame(self)
        text_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.last_text = tk.Text(
            text_wrap, wrap=tk.WORD, height=5, font=("Segoe UI", 9),
            background="#FAFAFA", relief=tk.FLAT, borderwidth=1,
        )
        scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.last_text.yview)
        # Editable so the user can fix a misheard word before polishing —
        # matches the webapp's transcript box. Edits flow back to the
        # owner via on_text_changed so Polish (which reads this panel's
        # widget directly) and the append-merge machinery both pick them up.
        self.last_text.configure(yscrollcommand=scroll.set)
        self.last_text.bind("<KeyRelease>", self._on_key_release)
        self.last_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------------ display

    def get_text(self) -> str:
        return self.last_text.get("1.0", "end-1c")

    def set_text(self, text: Optional[str]) -> None:
        self.last_text.delete("1.0", tk.END)
        if text:
            self.last_text.insert(tk.END, text)
            self.copy_last_btn.config(text="📋 Copy")
        self.set_enabled(bool(text))

    def clear(self) -> None:
        self.set_text(None)

    def set_enabled(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        self.copy_last_btn.state(state)
        self.reset_btn.state(state)

    def set_append_checked(self, enabled: bool) -> None:
        """Mirror an external append-mode change (tray menu / hotkey)
        into the checkbox without re-firing on_append_toggle."""
        try:
            self._suppress_append_trace = True
            self.append_var.set(bool(enabled))
        finally:
            self._suppress_append_trace = False

    # ------------------------------------------------------------ events

    def _on_key_release(self, _event: object = None) -> None:
        text = self.get_text()
        self.set_enabled(bool(text))
        self._on_text_changed(text)

    def _handle_append_toggle(self) -> None:
        if self._suppress_append_trace:
            return
        self._on_append_toggle(bool(self.append_var.get()))

    def _copy_last(self) -> None:
        text = self.get_text()
        if not text:
            return
        try:
            pyperclip.copy(text)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")
            return
        self.copy_last_btn.config(text="✓ Copied")
        self.after(1500, lambda: self.copy_last_btn.config(text="📋 Copy"))
