"""Runtime diagnostics — log capture and port-owner introspection.

Two pieces, both pure data (no UI imports):

- ``RingLogHandler`` keeps the last N formatted Python logging lines in
  memory so a GUI can surface what the app has been doing without parsing
  files. Attached once to the root logger from ``cli.main``.

- ``port_owner`` and ``infer_backend`` answer "who is actually serving on
  port 8090, and is it on CUDA or CPU?". Useful when the tray is sharing
  a server it didn't spawn (``mode: external`` or a stale leftover) and
  whisper.cpp's own startup log is therefore unavailable.
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, List, Optional

# Third-party imports
try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover — psutil is in requirements.txt
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_RING_CAPACITY = 500

BACKEND_CUDA = "CUDA"
BACKEND_CPU = "CPU"
BACKEND_UNKNOWN = "unknown"
BACKEND_CUDA_BUILD = "CUDA-capable build (runtime backend not confirmed)"


# --------------------------------------------------------------------- logging


class RingLogHandler(logging.Handler):
    """Thread-safe in-memory ring buffer of formatted log lines."""

    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY) -> None:
        super().__init__()
        self._buffer: Deque[str] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001 — logging contract: never raise
            self.handleError(record)
            return
        with self._lock:
            self._buffer.append(line)

    def lines(self) -> List[str]:
        with self._lock:
            return list(self._buffer)


_handler_lock = threading.Lock()
_handler: Optional[RingLogHandler] = None


def app_log_handler() -> RingLogHandler:
    """Return the singleton handler, creating it on first call."""
    global _handler
    with _handler_lock:
        if _handler is None:
            h = RingLogHandler()
            h.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            _handler = h
        return _handler


def attach_app_log_handler() -> None:
    """Idempotently attach the ring handler to the root logger."""
    h = app_log_handler()
    root = logging.getLogger()
    if h not in root.handlers:
        root.addHandler(h)


# ------------------------------------------------------------- port introspection


@dataclass
class PortOwner:
    pid: int
    name: str = ""
    exe: str = ""
    cmdline: List[str] = field(default_factory=list)
    exe_dir_files: List[str] = field(default_factory=list)

    def cmdline_str(self) -> str:
        return " ".join(self.cmdline) if self.cmdline else ""

    def has_no_gpu_flag(self) -> bool:
        cmd = self.cmdline_str()
        return "--no-gpu" in cmd or " -ng " in f" {cmd} " or cmd.endswith(" -ng")

    def has_cuda_dlls(self) -> bool:
        return any(
            name.lower().startswith(("cublas", "cudart", "ggml-cuda"))
            for name in self.exe_dir_files
        )


def port_owner(port: int) -> Optional[PortOwner]:
    """Best-effort lookup of the process LISTENing on ``port``.

    Returns ``None`` when psutil is unavailable, the lookup is denied
    (Windows can refuse cross-user inspection), or no listener is found.
    A returned ``PortOwner`` may have empty ``exe``/``cmdline`` if psutil
    could see the connection but not the owning process's metadata.
    """
    if psutil is None:
        return None

    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError, OSError) as exc:
        logger.debug(f"port_owner: net_connections denied ({exc})")
        return None

    for conn in connections:
        try:
            if not conn.laddr or conn.laddr.port != port:
                continue
            if conn.status != psutil.CONN_LISTEN:
                continue
            if conn.pid is None:
                continue
        except AttributeError:
            continue

        owner = PortOwner(pid=int(conn.pid))
        try:
            proc = psutil.Process(conn.pid)
            owner.name = proc.name() or ""
            try:
                owner.exe = proc.exe() or ""
            except (psutil.AccessDenied, FileNotFoundError):
                owner.exe = ""
            try:
                owner.cmdline = list(proc.cmdline() or [])
            except (psutil.AccessDenied, FileNotFoundError):
                owner.cmdline = []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        if owner.exe:
            try:
                owner.exe_dir_files = sorted(os.listdir(Path(owner.exe).parent))
            except OSError:
                owner.exe_dir_files = []
        return owner

    return None


def infer_backend(owner: Optional[PortOwner], server_log_lines: List[str]) -> str:
    """Best-effort CUDA/CPU verdict.

    Authoritative source is whisper.cpp's own ``whisper_backend_init`` log
    line — only available when this app spawned the server. For external
    servers we fall back to cmdline flags (``-ng`` / ``--no-gpu`` proves
    CPU) and to checking whether cuBLAS DLLs sit next to the binary
    (suggests a CUDA build, but doesn't prove it's actually using the GPU).
    """
    for line in reversed(server_log_lines):
        if "whisper_backend_init" in line:
            if "CUDA" in line:
                return BACKEND_CUDA
            if "CPU" in line:
                return BACKEND_CPU

    if owner is None:
        return BACKEND_UNKNOWN

    if owner.has_no_gpu_flag():
        return BACKEND_CPU

    if owner.has_cuda_dlls():
        return BACKEND_CUDA_BUILD

    return BACKEND_UNKNOWN
