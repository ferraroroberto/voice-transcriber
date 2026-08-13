"""Whisper-server process manager — mirrors `local-llm-hub/src/backend_process.py`.

One singleton process per project, keyed by the sibling `whisper_server.yaml`.
The port is fixed, so the OS guarantees mutual exclusion: if another project
(or a manual run) already holds the port, `start()` returns cleanly with
`already running (external)` and ownership is reported as EXTERNAL — the
caller must not kill it on exit.
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import re
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# Third-party imports
import requests
import yaml

from src.process_supervisor import (
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    is_port_in_use,
    stop_popen,
    wait_until_ready,
)

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - psutil is listed in the root requirements
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

# Valid values for `mode` in whisper_server.yaml. Default is MODE_EXTERNAL
# (issue #131) — MODE_LOCAL hard-fails at the next (re)start if a sibling
# project happens to be holding the mutex-shared port, which is a silent
# regression waiting to happen; only the committed whisper_server.yaml is
# authoritative, this default is just the safe fallback for a config that
# omits `mode` entirely.
MODE_LOCAL = "local"        # we own the server — refuse if host:port is externally held
MODE_EXTERNAL = "external"  # reuse an already-running server if present, else spawn
_VALID_MODES = (MODE_LOCAL, MODE_EXTERNAL)


@dataclass(frozen=True)
class ServerConfig:
    host: str
    bind_host: str
    port: int
    binary_path: Path
    model_path: Path
    args: List[str]
    pid_file: Path
    log_ring_size: int
    startup_timeout_seconds: float
    poll_interval_seconds: float
    request_timeout_seconds: float
    project_root: Path
    mode: str = MODE_EXTERNAL

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class ServerStatus:
    running: bool
    ownership: str  # OWNERSHIP_* constant
    pid: Optional[int] = None
    port: Optional[int] = None
    base_url: Optional[str] = None
    detail: str = ""


@dataclass
class ServerDescription:
    """Everything a user might want to see to confirm *what* is serving.

    Populated from (a) the static config (model path, args), (b) the model
    file on disk (size), (c) the running process (RSS memory, if ours) and
    (d) the captured whisper.cpp startup log lines (backend, params). Any
    field may be `None` when the data is not available (e.g. server not
    running, log not yet drained, psutil missing).
    """

    # --- static config --------------------------------------------------
    mode: str
    model_path: Path
    model_filename: str
    model_display_name: str
    model_size_bytes: Optional[int]
    model_exists: bool
    binary_path: Path
    binary_exists: bool
    host: str
    port: int
    base_url: str
    extra_args: List[str]
    threads: Optional[int]
    processors: Optional[int]
    inference_path: Optional[str]

    # --- runtime --------------------------------------------------------
    pid: Optional[int]
    ownership: str
    running: bool
    process_rss_bytes: Optional[int]
    # Parsed from whisper.cpp's startup log lines — see `_parse_runtime_info`.
    runtime_info: Dict[str, str]

    def summary_line(self) -> str:
        """One-line human summary — used in GUI status labels and toasts."""
        mem = (
            f", {_human_bytes(self.process_rss_bytes)} RSS"
            if self.process_rss_bytes is not None
            else ""
        )
        size = (
            f" ({_human_bytes(self.model_size_bytes)})"
            if self.model_size_bytes is not None
            else ""
        )
        return f"{self.model_display_name}{size}{mem}"

    def multiline(self) -> List[str]:
        """Multi-line block used by the CLI `server status` output."""
        lines: List[str] = []
        lines.append(f"📐 mode      : {self.mode}")
        lines.append(f"🧠 model     : {self.model_display_name}")
        lines.append(f"   path      : {self.model_path}")
        if self.model_size_bytes is not None:
            lines.append(f"   size      : {_human_bytes(self.model_size_bytes)}")
        elif not self.model_exists:
            lines.append("   size      : (file missing)")
        lines.append(f"🛠️  binary    : {self.binary_path}"
                     + ("" if self.binary_exists else "  (missing)"))
        lines.append(f"🌐 endpoint  : {self.base_url}"
                     + (self.inference_path or ""))
        thread_bits = []
        if self.threads is not None:
            thread_bits.append(f"threads={self.threads}")
        if self.processors is not None:
            thread_bits.append(f"processors={self.processors}")
        if thread_bits:
            lines.append(f"⚙️  runtime   : {', '.join(thread_bits)}")
        if self.process_rss_bytes is not None:
            lines.append(f"💾 memory    : {_human_bytes(self.process_rss_bytes)} (pid {self.pid})")
        if self.runtime_info:
            for key, value in self.runtime_info.items():
                lines.append(f"   {key:<10}: {value}")
        return lines


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def _human_bytes(n: Optional[int]) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in _SIZE_UNITS:
        if size < 1024 or unit == _SIZE_UNITS[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


# Recognised lines in whisper.cpp's stderr on startup. The values carry
# exactly the information users want to eyeball to confirm the model:
# backend (CPU vs CUDA/Metal), model type + param count, languages. Keys are
# stable, human-readable labels; values are the tails of the matching lines.
_RUNTIME_PATTERNS: List[tuple[str, "re.Pattern[str]"]] = [
    ("backend",    re.compile(r"whisper_backend_init[^:]*:\s*(.+)$")),
    ("model type", re.compile(r"whisper_model_load:\s*type\s*=\s*(.+)$")),
    ("params",     re.compile(r"whisper_model_load:\s*(?:model\s+size|n_params)\s*=\s*(.+)$")),
    ("mem used",   re.compile(r"whisper_model_load:\s*model ctx\s*=\s*(.+)$")),
    ("languages",  re.compile(r"whisper_model_load:\s*n_langs\s*=\s*(.+)$")),
    ("system",     re.compile(r"system_info:\s*(.+)$")),
]


def _parse_runtime_info(log_lines: List[str]) -> Dict[str, str]:
    """Scan the captured server log for whisper.cpp's one-shot diagnostics.

    Later matches win — the server re-logs on each model reload, so the most
    recent line is the authoritative one.
    """
    found: Dict[str, str] = {}
    for line in log_lines:
        for label, pattern in _RUNTIME_PATTERNS:
            m = pattern.search(line)
            if m:
                found[label] = m.group(1).strip()
    return found


def _infer_display_name(model_filename: str) -> str:
    """`ggml-large-v3-turbo.bin` → `large-v3-turbo`."""
    stem = model_filename
    for suffix in (".bin", ".gguf"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem.startswith("ggml-"):
        stem = stem[len("ggml-"):]
    return stem or model_filename


def _parse_args_flags(args: List[str]) -> Dict[str, Optional[str]]:
    """Pull a handful of known flags out of the extra-args list."""
    out: Dict[str, Optional[str]] = {
        "threads": None,
        "processors": None,
        "inference-path": None,
    }
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--threads", "-t") and i + 1 < len(args):
            out["threads"] = args[i + 1]
            i += 2
            continue
        if token in ("--processors", "-p") and i + 1 < len(args):
            out["processors"] = args[i + 1]
            i += 2
            continue
        if token == "--inference-path" and i + 1 < len(args):
            out["inference-path"] = args[i + 1]
            i += 2
            continue
        i += 1
    return out


def _resolve_binary(project_root: Path, raw: str) -> Path:
    p = (project_root / raw).resolve()
    if p.exists():
        return p
    if sys.platform == "win32" and not raw.endswith(".exe"):
        alt = p.with_suffix(".exe")
        if alt.exists():
            return alt
    return p  # return the non-existent path; caller decides


def load_config(config_path: Optional[Path] = None) -> ServerConfig:
    """Load `whisper_server.yaml` from next to this file (or an override).

    The project root is the directory containing the `whisper_server/` folder.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent / "whisper_server.yaml"
    else:
        config_path = Path(config_path).resolve()

    project_root = Path(__file__).resolve().parent.parent.parent
    raw: Dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    server = raw.get("server") or {}
    binary = raw.get("binary") or {}
    model = raw.get("model") or {}
    health = raw.get("health") or {}

    mode = str(raw.get("mode", MODE_EXTERNAL)).strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(
            f"whisper_server.yaml `mode` must be one of {_VALID_MODES}, got {mode!r}"
        )

    return ServerConfig(
        host=str(server.get("host", "127.0.0.1")),
        bind_host=str(server.get("bind_host", "0.0.0.0")),
        port=int(server.get("port", 8090)),
        binary_path=_resolve_binary(project_root, str(binary.get("path", "vendor/whisper.cpp/whisper-server"))),
        model_path=(project_root / str(model.get("path", "vendor/whisper.cpp/models/ggml-large-v3-turbo.bin"))).resolve(),
        args=list(raw.get("args", []) or []),
        pid_file=(project_root / str(raw.get("pid_file", ".whisper_server.pid"))).resolve(),
        log_ring_size=int(raw.get("log_ring_size", 1000)),
        startup_timeout_seconds=float(health.get("startup_timeout_seconds", 60)),
        poll_interval_seconds=float(health.get("poll_interval_seconds", 0.5)),
        request_timeout_seconds=float(health.get("request_timeout_seconds", 1.5)),
        project_root=project_root,
        mode=mode,
    )


class WhisperServerManager:
    """Start / stop / health-check a local whisper.cpp server."""

    def __init__(self, config: Optional[ServerConfig] = None) -> None:
        self.config: ServerConfig = config or load_config()
        self._proc: Optional[subprocess.Popen] = None
        self._log: Deque[str] = deque(maxlen=self.config.log_ring_size)
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._session = requests.Session()

    # ------------------------------------------------------------------ status

    def is_reachable(self) -> bool:
        """HTTP health check — whisper.cpp server answers 200 on `/`."""
        url = self.config.base_url + "/"
        try:
            r = self._session.get(url, timeout=self.config.request_timeout_seconds)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def is_port_in_use(self) -> bool:
        """Low-level port probe — works even if HTTP is not yet listening."""
        return is_port_in_use(self.config.host, self.config.port)

    def status(self) -> ServerStatus:
        running_here = self._proc is not None and self._proc.poll() is None
        reachable = self.is_reachable() or self.is_port_in_use()

        if running_here and reachable:
            return ServerStatus(
                running=True,
                ownership=OWNERSHIP_OURS,
                pid=self._proc.pid,
                port=self.config.port,
                base_url=self.config.base_url,
                detail="running (started by this process)",
            )

        if reachable:
            pid = self._read_pid_file()
            return ServerStatus(
                running=True,
                ownership=OWNERSHIP_EXTERNAL,
                pid=pid,
                port=self.config.port,
                base_url=self.config.base_url,
                detail="running (external — started elsewhere)",
            )

        return ServerStatus(
            running=False,
            ownership=OWNERSHIP_NONE,
            port=self.config.port,
            base_url=self.config.base_url,
            detail="not running",
        )

    # ------------------------------------------------------------------- start

    def start(self, wait: bool = True) -> ServerStatus:
        """Start the server, honouring the configured `mode`.

        - `mode=local`: refuse to reuse an externally-held port. If
          something else is already bound to host:port, raise
          `RuntimeError` so the caller can surface the conflict.
        - `mode=external`: reuse an already-reachable server untouched;
          fall back to spawning our own when nothing is listening so the
          app still works without the sibling hub.

        Idempotent — returns the current status when the server is already
        ours.
        """
        current = self.status()
        if current.running and current.ownership == OWNERSHIP_OURS:
            logger.info(f"ℹ️  Whisper server already {current.detail}")
            return current
        if current.running:  # externally held
            if self.config.mode == MODE_EXTERNAL:
                logger.info(
                    f"🔗 Reusing external whisper-server at {current.base_url} "
                    "(mode=external)"
                )
                return current
            raise RuntimeError(
                f"❌ Port {self.config.port} is already in use by another process "
                f"(mode=local refuses to share). Stop the other server first, or "
                f"switch `mode: external` in whisper_server.yaml to reuse it."
            )

        self._validate_paths()

        cmd = self._build_command()
        logger.info(f"🚀 Starting whisper-server: {' '.join(str(c) for c in cmd)}")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if sys.platform == "win32":
            # Let the binary find sibling CUDA DLLs.
            env["PATH"] = str(self.config.binary_path.parent) + os.pathsep + env.get("PATH", "")

        try:
            popen_kwargs: Dict[str, Any] = dict(
                cwd=str(self.config.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                )
            self._proc = subprocess.Popen(cmd, **popen_kwargs)
        except FileNotFoundError as e:
            raise RuntimeError(
                f"❌ whisper-server binary not found: {self.config.binary_path}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"❌ failed to launch whisper-server: {e}") from e

        self._write_pid_file(self._proc.pid)
        self._reader = threading.Thread(
            target=self._drain_output,
            args=(self._proc,),
            daemon=True,
        )
        self._reader.start()

        if wait:
            self._wait_until_ready()

        return self.status()

    # -------------------------------------------------------------------- stop

    def stop(self) -> ServerStatus:
        """Stop the server we started. Never touches an EXTERNAL server."""
        status = self.status()
        if status.ownership == OWNERSHIP_EXTERNAL:
            logger.info("✋ Leaving external whisper-server running (not ours)")
            return status
        if not status.running or self._proc is None:
            logger.info("ℹ️  Whisper server was not running")
            self._clear_pid_file()
            return ServerStatus(
                running=False,
                ownership=OWNERSHIP_NONE,
                port=self.config.port,
                base_url=self.config.base_url,
                detail="not running",
            )

        p = self._proc
        try:
            stop_popen(p, name="whisper-server", terminate_timeout=8, kill_timeout=5)
        finally:
            self._proc = None
            self._clear_pid_file()

        return ServerStatus(
            running=False,
            ownership=OWNERSHIP_NONE,
            port=self.config.port,
            base_url=self.config.base_url,
            detail="stopped",
        )

    # -------------------------------------------------------------- diagnostics

    def log_lines(self) -> List[str]:
        with self._lock:
            return list(self._log)

    def describe(self, status: Optional[ServerStatus] = None) -> ServerDescription:
        """Collect everything that identifies *what* is serving.

        Safe to call when the server is not running — static fields come
        from the config, runtime fields are `None`. When the server is
        ours, process RSS is read via `psutil`; when it's external, we
        still show the config and whatever log lines we've captured.
        """
        if status is None:
            status = self.status()

        model_path = self.config.model_path
        try:
            model_size: Optional[int] = (
                model_path.stat().st_size if model_path.exists() else None
            )
        except OSError:
            model_size = None

        args_flags = _parse_args_flags(self.config.args)

        rss: Optional[int] = None
        target_pid = status.pid
        if target_pid is not None and psutil is not None:
            try:
                rss = psutil.Process(target_pid).memory_info().rss
            except (psutil.Error, OSError):
                rss = None

        runtime_info = _parse_runtime_info(self.log_lines())

        return ServerDescription(
            mode=self.config.mode,
            model_path=model_path,
            model_filename=model_path.name,
            model_display_name=_infer_display_name(model_path.name),
            model_size_bytes=model_size,
            model_exists=model_path.exists(),
            binary_path=self.config.binary_path,
            binary_exists=self.config.binary_path.exists(),
            host=self.config.host,
            port=self.config.port,
            base_url=self.config.base_url,
            extra_args=list(self.config.args),
            threads=int(args_flags["threads"]) if args_flags["threads"] else None,
            processors=int(args_flags["processors"]) if args_flags["processors"] else None,
            inference_path=args_flags["inference-path"],
            pid=status.pid,
            ownership=status.ownership,
            running=status.running,
            process_rss_bytes=rss,
            runtime_info=runtime_info,
        )

    # ------------------------------------------------------------------ helpers

    def _validate_paths(self) -> None:
        if not self.config.binary_path.exists():
            raise RuntimeError(
                f"❌ whisper-server binary not found at {self.config.binary_path}. "
                f"Build or install whisper.cpp into "
                f"{self.config.binary_path.parent.relative_to(self.config.project_root)}."
            )
        if not self.config.model_path.exists():
            raise RuntimeError(
                f"❌ whisper model file not found at {self.config.model_path}. "
                f"Download it with whisper.cpp's `download-ggml-model` script."
            )

    def _build_command(self) -> List[str]:
        cmd: List[str] = [
            str(self.config.binary_path),
            "--host", self.config.bind_host,
            "--port", str(self.config.port),
            "--model", str(self.config.model_path),
        ]
        cmd.extend(self.config.args)
        return cmd

    def _drain_output(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            with self._lock:
                self._log.append(line)

    def _wait_until_ready(self) -> None:
        def _not_alive_message() -> str:
            tail = "\n".join(self.log_lines()[-20:])
            return f"❌ whisper-server exited before becoming ready.\nLast output:\n{tail}"

        wait_until_ready(
            still_alive=lambda: self._proc is not None and self._proc.poll() is None,
            is_reachable=self.is_reachable,
            timeout_seconds=self.config.startup_timeout_seconds,
            poll_interval_seconds=self.config.poll_interval_seconds,
            not_alive_message=_not_alive_message,
            timeout_message=(
                f"❌ whisper-server did not become ready within "
                f"{self.config.startup_timeout_seconds}s"
            ),
        )
        logger.info(f"✅ Whisper server ready at {self.config.base_url}")

    def _write_pid_file(self, pid: int) -> None:
        try:
            self.config.pid_file.write_text(str(pid), encoding="utf-8")
        except OSError as e:
            logger.warning(f"⚠️  Could not write PID file {self.config.pid_file}: {e}")

    def _clear_pid_file(self) -> None:
        try:
            if self.config.pid_file.exists():
                self.config.pid_file.unlink()
        except OSError as e:
            logger.warning(f"⚠️  Could not remove PID file {self.config.pid_file}: {e}")

    def _read_pid_file(self) -> Optional[int]:
        try:
            if self.config.pid_file.exists():
                return int(self.config.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return None
