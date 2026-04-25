"""Compact recording popup — a borderless Toplevel with a VU meter.

Used by both the tray-hotkey flow and the main-window "Record" button. The
popup owns its own recorder thread; when it closes it returns the captured
audio (or None) to the caller through a callback.
"""

from __future__ import annotations

# Standard library imports
import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core import AudioRecorder, RecordingError
from core.recorder import Recording

logger = logging.getLogger(__name__)

DoneCallback = Callable[[Optional[Recording], Optional[str]], None]


class RecordingPopup:
    """Tiny top-most window that shows elapsed time + level while recording."""

    def __init__(
        self,
        parent: Optional[tk.Misc],
        recorder: AudioRecorder,
        max_seconds: int,
        on_done: DoneCallback,
        hotkey_label: Optional[str] = None,
    ) -> None:
        self.recorder = recorder
        self.max_seconds = max_seconds
        self.on_done = on_done
        self.hotkey_label = hotkey_label
        self._queue: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None

        self.window = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.window.title("Recording")
        self.window.attributes("-topmost", True)
        self.window.overrideredirect(True)
        self.window.configure(background="#1e1e1e")
        self._layout()
        self._center(width=320, height=130)
        self.window.protocol("WM_DELETE_WINDOW", self.stop)

        # Esc / Enter are local fallbacks when the popup has focus. The
        # canonical stop is the configured global hotkey owned by the
        # launching app, so both "tray" and "gui" modes behave the same way.
        self.window.bind("<Escape>", lambda _e: self.stop())
        self.window.bind("<Return>", lambda _e: self.stop())

        self._start_worker()
        self.window.after(50, self._pump_queue)

    # ------------------------------------------------------------ layout

    def _layout(self) -> None:
        frame = tk.Frame(self.window, background="#1e1e1e", padx=16, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        self.time_label = tk.Label(
            frame, text="🎤 00:00", font=("Segoe UI", 14, "bold"),
            foreground="#ffffff", background="#1e1e1e",
        )
        self.time_label.pack(anchor="w")

        self.level_var = tk.DoubleVar(value=0.0)
        self.level_bar = ttk.Progressbar(frame, variable=self.level_var, maximum=100, length=280)
        self.level_bar.pack(pady=(8, 8), fill=tk.X)

        hint_text = (
            f"{self.hotkey_label} to stop (Enter/Esc also work)"
            if self.hotkey_label
            else "Enter/Esc to stop"
        )
        self.hint_label = tk.Label(
            frame,
            text=hint_text,
            font=("Segoe UI", 9),
            foreground="#aaaaaa",
            background="#1e1e1e",
        )
        self.hint_label.pack(anchor="w")

    def _center(self, width: int, height: int) -> None:
        sw = self.window.winfo_screenwidth()
        sh = self.window.winfo_screenheight()
        x = (sw - width) // 2
        y = int(sh * 0.75)  # near the bottom so it doesn't cover the cursor
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------ worker

    def _start_worker(self) -> None:
        def run() -> None:
            def progress(remaining: float, level: float) -> None:
                self._queue.put(("progress", (remaining, level)))

            try:
                result = self.recorder.record(
                    max_seconds=self.max_seconds,
                    progress=progress,
                )
                self._queue.put(("done", result))
            except RecordingError as e:
                self._queue.put(("error", str(e)))
            except Exception as e:
                logger.exception("recording worker crashed")
                self._queue.put(("error", str(e)))

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _pump_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    remaining, level = payload
                    elapsed = max(0.0, self.max_seconds - remaining)
                    mm, ss = divmod(int(elapsed), 60)
                    self.time_label.config(text=f"🎤 {mm:02d}:{ss:02d}")
                    self.level_var.set(min(100.0, level * 100.0 * 2))  # scale for visibility
                elif kind == "done":
                    self._finish(payload, None)
                    return
                elif kind == "error":
                    self._finish(None, payload)
                    return
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            self.window.after(50, self._pump_queue)

    # ------------------------------------------------------------ control

    def stop(self) -> None:
        self.recorder.request_stop()

    def _finish(self, recording: Optional[Recording], error: Optional[str]) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        try:
            self.on_done(recording, error)
        except Exception:
            logger.exception("on_done callback raised")
