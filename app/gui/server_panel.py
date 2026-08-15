"""Server status panel — traffic light, model identity, start/stop
buttons, and the diagnostics / model-details dialogs.

Extracted from ``TranscriberApp`` (voice-transcriber#177) so the god
class no longer owns whisper-server lifecycle display alongside
everything else. Self-contained: only needs the ``WhisperServerManager``
it displays, no back-reference to the owning window.
"""

from __future__ import annotations

# Standard library imports
import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from src.whisper_server import OWNERSHIP_OURS, WhisperServerManager
from .diagnostics_window import DiagnosticsWindow

logger = logging.getLogger(__name__)


class ServerPanel(ttk.Frame):
    """Server status row, model line, and start/stop controls."""

    def __init__(self, parent: tk.Misc, server: WhisperServerManager) -> None:
        super().__init__(parent)
        self.server = server
        self.status_var = tk.StringVar(value="checking…")
        self.model_var = tk.StringVar(value="model: …")
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 6}

        status_frame = ttk.Frame(self)
        status_frame.pack(fill=tk.X, **pad)
        ttk.Label(status_frame, text="Server:").pack(side=tk.LEFT)
        self.status_label = ttk.Label(
            status_frame, textvariable=self.status_var, font=("Segoe UI", 10, "bold"),
        )
        self.status_label.pack(side=tk.LEFT, padx=8)

        # Model identity — users want to confirm at a glance *which* whisper
        # build is actually serving (e.g. large-v3-turbo, size on disk, RSS).
        model_frame = ttk.Frame(self)
        model_frame.pack(fill=tk.X, **pad)
        ttk.Label(model_frame, textvariable=self.model_var).pack(side=tk.LEFT)
        ttk.Button(
            model_frame, text="🩺 Diagnostics", command=self.show_diagnostics, width=14,
        ).pack(side=tk.RIGHT)
        ttk.Button(
            model_frame, text="ℹ Details", command=self.show_model_details, width=10,
        ).pack(side=tk.RIGHT, padx=(0, 4))

        server_btn_frame = ttk.Frame(self)
        server_btn_frame.pack(fill=tk.X, **pad)
        self.start_btn = ttk.Button(server_btn_frame, text="▶ Start server", command=self.start_server)
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        self.stop_btn = ttk.Button(server_btn_frame, text="■ Stop server", command=self.stop_server)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

    # ------------------------------------------------------------ status

    def refresh(self) -> None:
        """Re-query the server and update the traffic light, model line,
        and start/stop button availability. Called on the owner's poll
        tick (window lifecycle stays with TranscriberApp)."""
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
        self.stop_btn.state(
            ["!disabled"] if status.running and status.ownership == OWNERSHIP_OURS else ["disabled"]
        )

    # ------------------------------------------------------------ actions

    def start_server(self) -> None:
        self.status_var.set("⏳ starting…")
        threading.Thread(target=self._start_server_worker, daemon=True).start()

    def _start_server_worker(self) -> None:
        try:
            self.server.start()
        except RuntimeError as e:
            msg = str(e)
            logger.error(msg)
            self.after(0, lambda m=msg: messagebox.showerror("Server failed to start", m))

    def stop_server(self) -> None:
        threading.Thread(target=self.server.stop, daemon=True).start()

    # ------------------------------------------------------------ dialogs

    def show_diagnostics(self) -> None:
        DiagnosticsWindow(self, self.server)

    def show_model_details(self) -> None:
        description = self.server.describe()
        win = tk.Toplevel(self)
        win.title("Whisper model details")
        win.geometry("560x360")
        win.transient(self.winfo_toplevel())
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
