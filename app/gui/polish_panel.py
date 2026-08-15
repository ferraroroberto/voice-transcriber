"""Polish panel — style/model pickers, the polished-text box, and the
polish / save-defaults / show-prompt / copy-polished actions.

Extracted from ``TranscriberApp`` (voice-transcriber#177). Needs
read-only access to the current transcript (``get_transcript_text``,
normally ``TranscriptPanel.get_text``) but never mutates it — the
polish flow is one-way, transcript → polished text.
"""

from __future__ import annotations

# Standard library imports
import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

# Third-party imports
import pyperclip

from src import AppConfig
from src.polish import PolishClient, PolishError
from src.polish_prompts import PolishPrompt, get_prompt, load_polish_prompts
from src.webapp_config import WebappConfig, update_webapp_config

logger = logging.getLogger(__name__)


class PolishPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        webapp_config: WebappConfig,
        config: AppConfig,
        get_transcript_text: Callable[[], str],
    ) -> None:
        super().__init__(parent)
        self.webapp_config = webapp_config
        self.config = config
        self._get_transcript_text = get_transcript_text
        self.polish_client = PolishClient(self.webapp_config.llm_hub_url)
        # Multi-prompt polish: load the library at boot. Drop-down selection
        # mirrors the webapp's "Polish style" picker.
        self.polish_prompts = load_polish_prompts()
        self._prompt_label_to_id = {p.label: p.id for p in self.polish_prompts}
        default_prompt = get_prompt(self.webapp_config.polish_prompt_default, self.polish_prompts)
        self._last_polished: Optional[str] = None

        self.model_var = tk.StringVar(value=self.webapp_config.polish_model_default)
        self.style_var = tk.StringVar(value=default_prompt.label)
        self._build_widgets()

    def _build_widgets(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Label(header, text="✨ Polish:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        self.model_combo = ttk.Combobox(
            header, textvariable=self.model_var, state="readonly", width=18,
            values=tuple(self.webapp_config.polish_models_available),
        )
        self.model_combo.pack(side=tk.LEFT, padx=(8, 4))

        self.style_combo = ttk.Combobox(
            header, textvariable=self.style_var, state="readonly", width=18,
            values=tuple(p.label for p in self.polish_prompts),
        )
        self.style_combo.pack(side=tk.LEFT)

        self.polish_btn = ttk.Button(header, text="✨ Polish", width=10, command=self.run_polish)
        self.polish_btn.pack(side=tk.RIGHT)
        self.polish_btn.state(["disabled"])

        text_wrap = ttk.Frame(self)
        text_wrap.pack(fill=tk.X, pady=(4, 0))
        self.polished_text = tk.Text(
            text_wrap, wrap=tk.WORD, height=4, font=("Segoe UI", 9),
            background="#F0F4FA", relief=tk.FLAT, borderwidth=1,
        )
        scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.polished_text.yview)
        self.polished_text.configure(yscrollcommand=scroll.set, state=tk.DISABLED)
        self.polished_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(
            actions, text="⭐ Save defaults", width=15, command=self.save_defaults,
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="👁 Show prompt", width=14, command=self.show_prompt,
        ).pack(side=tk.LEFT, padx=(6, 0))
        self.copy_polished_btn = ttk.Button(actions, text="📋 Copy polished", command=self.copy_polished)
        self.copy_polished_btn.pack(side=tk.RIGHT)
        self.copy_polished_btn.state(["disabled"])

    # ------------------------------------------------------------ display

    def clear(self) -> None:
        """Drop any polished output — called by the owner whenever the
        source transcript changes underneath (a fresh take, Reset)."""
        self._last_polished = None
        self._render("")
        self.copy_polished_btn.state(["disabled"])

    def set_enabled(self, enabled: bool) -> None:
        self.polish_btn.state(["!disabled"] if enabled else ["disabled"])

    def _render(self, text: str) -> None:
        self.polished_text.configure(state=tk.NORMAL)
        self.polished_text.delete("1.0", tk.END)
        if text:
            self.polished_text.insert(tk.END, text)
        self.polished_text.configure(state=tk.DISABLED)

    def _current_prompt(self) -> PolishPrompt:
        """Resolve the dropdown's label back to a PolishPrompt entry."""
        label = self.style_var.get()
        pid = self._prompt_label_to_id.get(label)
        return get_prompt(pid, self.polish_prompts)

    # ------------------------------------------------------------ actions

    def run_polish(self) -> None:
        text = self._get_transcript_text()
        if not text:
            return
        model = self.model_var.get()
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
            self.after(0, lambda m=msg: messagebox.showerror("Polish failed", m))
            self.after(0, self._reset_button)
            return
        self._last_polished = result.polished_text
        self.after(0, lambda: self._render(result.polished_text))
        self.after(0, lambda: self.copy_polished_btn.state(["!disabled"]))
        # Auto-copy polished text + flash the button so the user knows it
        # already landed on the clipboard — matches the webapp's behaviour
        # and saves a manual click on every polish.
        if self.config.auto_copy:
            try:
                pyperclip.copy(result.polished_text)
                self.after(0, self._flash_copied)
            except Exception as exc:
                logger.warning(f"⚠️  Auto-copy of polished failed: {exc}")
        self.after(0, self._reset_button)

    def _reset_button(self) -> None:
        self.polish_btn.state(["!disabled"])
        self.polish_btn.config(text="✨ Polish")

    def copy_polished(self) -> None:
        if not self._last_polished:
            return
        try:
            pyperclip.copy(self._last_polished)
        except Exception as exc:
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")
            return
        self._flash_copied()

    def _flash_copied(self) -> None:
        self.copy_polished_btn.config(text="✓ Copied")
        self.after(1500, lambda: self.copy_polished_btn.config(text="📋 Copy polished"))

    def save_defaults(self) -> None:
        model = self.model_var.get()
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

    def show_prompt(self) -> None:
        """Read-only popup with the system prompt about to be sent."""
        prompt = self._current_prompt()
        win = tk.Toplevel(self)
        win.title(f"Polish prompt — {prompt.label}")
        win.geometry("640x420")
        win.transient(self.winfo_toplevel())
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
