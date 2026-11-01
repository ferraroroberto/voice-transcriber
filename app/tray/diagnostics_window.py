"""Diagnostics window — what's actually serving on port 8090.

Surfaces three things the main view deliberately keeps off-screen:

- Configured mode vs runtime ownership (catches `mode: local` silently
  binding to a leftover server on the same port).
- Backend verdict (CUDA / CPU / unknown), inferred from whisper.cpp's
  own startup log when we spawned the server, otherwise from the port
  owner's cmdline and bundled DLLs.
- Two scrollable tabs: the captured whisper-server stdout (when ours)
  and the in-memory app log (always).

Opened from the main window's "🩺 Diagnostics" button. Auto-refreshes
while open; closing stops the refresh loop.
"""

from __future__ import annotations

# Standard library imports
import logging
import tkinter as tk
from tkinter import ttk
from typing import List, Optional

# Third-party imports
import pyperclip

from src import (
    BACKEND_CPU,
    BACKEND_UNKNOWN,
    PortOwner,
    app_log_handler,
    infer_backend,
    port_owner,
)
from src.whisper_server import (
    MODE_LOCAL,
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_OURS,
    WhisperServerManager,
)

logger = logging.getLogger(__name__)

REFRESH_MS = 1500


class DiagnosticsWindow:
    """Toplevel diagnostics view, owned by the parent main window."""

    def __init__(self, parent: tk.Misc, manager: WhisperServerManager) -> None:
        self.manager = manager
        self.win = tk.Toplevel(parent)
        self.win.title("Diagnostics")
        self.win.geometry("820x620")
        self.win.transient(parent)
        self.win.minsize(640, 480)

        self._closed = False
        self._summary_var = tk.StringVar(value="")
        self._build_widgets()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()

    # ---------------------------------------------------------------- layout

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.win)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        summary = tk.Text(
            outer,
            wrap=tk.WORD,
            height=10,
            font=("Consolas", 9),
            relief=tk.FLAT,
            background="#F4F4F4",
            borderwidth=1,
        )
        summary.pack(fill=tk.X, pady=(0, 8))
        summary.configure(state=tk.DISABLED)
        self._summary = summary

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)
        self._server_log = self._add_log_tab(notebook, "Server log (whisper-server)")
        self._app_log = self._add_log_tab(notebook, "App log")

        btns = ttk.Frame(outer)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="🔄 Refresh now", command=self._refresh_once).pack(side=tk.LEFT)
        ttk.Button(btns, text="📋 Copy report", command=self._copy_report).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text="Close", command=self._on_close).pack(side=tk.RIGHT)

    @staticmethod
    def _add_log_tab(notebook: ttk.Notebook, title: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = tk.Text(frame, wrap=tk.NONE, font=("Consolas", 9))
        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set, state=tk.DISABLED)
        text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return text

    # ------------------------------------------------------------- lifecycle

    def _on_close(self) -> None:
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _refresh(self) -> None:
        if self._closed:
            return
        self._refresh_once()
        self.win.after(REFRESH_MS, self._refresh)

    def _refresh_once(self) -> None:
        if self._closed:
            return
        try:
            report = self._collect_report()
        except Exception as exc:  # noqa: BLE001 — diagnostics must never crash the app
            logger.warning(f"⚠️  Diagnostics refresh failed: {exc}")
            return

        self._set_text(self._summary, report.summary)
        self._set_text(self._server_log, "\n".join(report.server_log) or "(empty — server not spawned by this app)")
        self._set_text(self._app_log, "\n".join(report.app_log))

    # ------------------------------------------------------------- data

    def _collect_report(self) -> "DiagnosticsReport":
        status = self.manager.status()
        description = self.manager.describe(status=status)
        server_log = self.manager.log_lines()

        owner: Optional[PortOwner] = None
        if status.running:
            owner = port_owner(self.manager.config.port)

        backend = infer_backend(owner, server_log)
        app_log = app_log_handler().lines()

        summary_lines: List[str] = []
        summary_lines.append("Voice Transcription — Diagnostics")
        summary_lines.append("")
        summary_lines.append(f"Configured mode : {self.manager.config.mode}")
        summary_lines.append(f"Runtime         : {status.detail}")
        summary_lines.append(f"Endpoint        : {status.base_url}")
        summary_lines.append(f"Backend         : {backend}")

        if (
            self.manager.config.mode == MODE_LOCAL
            and status.ownership == OWNERSHIP_EXTERNAL
        ):
            summary_lines.append("")
            summary_lines.append(
                "⚠️  mode is `local` but the port is held by another process."
            )
            summary_lines.append(
                "    This app's flags (e.g. --flash-attn) are NOT in effect."
            )
            summary_lines.append(
                "    Stop the other process or kill its PID below, then restart."
            )

        if backend == BACKEND_CPU:
            summary_lines.append("")
            summary_lines.append(
                "⚠️  Backend is CPU. Transcription will be slow on any model."
            )
        elif backend == BACKEND_UNKNOWN and status.ownership == OWNERSHIP_EXTERNAL:
            summary_lines.append("")
            summary_lines.append(
                "ℹ️  Backend unknown: the server was started by another process,"
            )
            summary_lines.append(
                "    so whisper.cpp's startup log is unavailable to this app."
            )

        summary_lines.append("")
        summary_lines.append(f"Model           : {description.model_display_name}")
        summary_lines.append(f"  path          : {description.model_path}")
        summary_lines.append(f"  args (config) : {' '.join(description.extra_args)}")

        if status.ownership == OWNERSHIP_OURS and description.runtime_info:
            summary_lines.append("")
            summary_lines.append("whisper.cpp runtime:")
            for key, value in description.runtime_info.items():
                summary_lines.append(f"  {key:<11}: {value}")

        if owner is not None:
            summary_lines.append("")
            summary_lines.append("Port owner (psutil):")
            summary_lines.append(f"  pid           : {owner.pid}")
            if owner.name:
                summary_lines.append(f"  name          : {owner.name}")
            if owner.exe:
                summary_lines.append(f"  exe           : {owner.exe}")
            if owner.cmdline:
                summary_lines.append(f"  cmdline       : {owner.cmdline_str()}")
            if owner.has_no_gpu_flag():
                summary_lines.append("  ⚠️ -ng / --no-gpu present — server is forced to CPU")
            if owner.exe and not owner.has_cuda_dlls():
                summary_lines.append("  (no cuBLAS DLLs next to the binary — CPU build likely)")
        elif status.running:
            summary_lines.append("")
            summary_lines.append(
                "Port owner       : (psutil could not identify the listening pid)"
            )

        return DiagnosticsReport(
            summary="\n".join(summary_lines),
            server_log=server_log,
            app_log=app_log,
        )

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _set_text(widget: tk.Text, content: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, content)
        widget.configure(state=tk.DISABLED)
        widget.see(tk.END)

    def _copy_report(self) -> None:
        try:
            report = self._collect_report()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️  Diagnostics copy failed: {exc}")
            return
        blob = (
            report.summary
            + "\n\n--- whisper-server log ---\n"
            + "\n".join(report.server_log)
            + "\n\n--- app log ---\n"
            + "\n".join(report.app_log)
        )
        try:
            pyperclip.copy(blob)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️  Clipboard copy failed: {exc}")


class DiagnosticsReport:
    """Snapshot returned by ``_collect_report`` — plain data only."""

    __slots__ = ("summary", "server_log", "app_log")

    def __init__(self, summary: str, server_log: List[str], app_log: List[str]) -> None:
        self.summary = summary
        self.server_log = server_log
        self.app_log = app_log
