"""Owns the tray's three background service lifecycles.

Extracted from ``TrayApp`` (voice-transcriber#177): the whisper-server
subprocess, the uvicorn webapp, and the optional named cloudflared tunnel
in front of it are started together on launch and torn down together
(tunnel first, so the public URL goes 5xx immediately) on quit.
``TrayApp`` supplies a ``notify`` callback for the toasts each lifecycle
transition surfaces, and reads status/labels back through this object
instead of touching ``WhisperServerManager``/``WebappManager`` directly.
"""

from __future__ import annotations

# Standard library imports
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from src import AppConfig
from src.process_supervisor import stop_popen
from src.tunnel import (
    CloudflaredNotFoundError,
    persist_tunnel_url,
    publish_refusal_reason,
    read_tunnel_hostname,
    remove_tunnel_url_file,
    spawn_cloudflared,
)
from src.webapp_config import load_webapp_config
from src.whisper_server import OWNERSHIP_OURS, WhisperServerManager
from app.webapp.manager import WebappManager, load_config as load_webapp_runtime_config

logger = logging.getLogger(__name__)

Notify = Callable[[str, str], None]


def current_auth_token() -> str:
    """Re-read the bearer token at call-time so a freshly rotated token
    lands in a copied URL (or a tunnel start) without needing a tray
    restart."""
    try:
        return load_webapp_config().auth_token
    except Exception:
        return ""


class ServiceSupervisor:
    """Starts, stops, and reports status for whisper-server, the webapp,
    and the cloudflared tunnel — the tray's three background subsystems."""

    _MODEL_LABEL_TTL = 2.0

    def __init__(
        self,
        config: AppConfig,
        tunnel_config_path: Path,
        tunnel_url_file: Path,
        project_root: Path,
        notify: Notify,
    ) -> None:
        self.config = config
        self.server = WhisperServerManager()
        self.webapp = WebappManager(load_webapp_runtime_config(config.webapp))
        self._notify = notify
        self._tunnel_config_path = tunnel_config_path
        self._tunnel_url_file = tunnel_url_file
        self._project_root = project_root
        self._cloudflared_proc: Optional[subprocess.Popen] = None
        # Cloudflare named tunnel — auto-spawned alongside whisper + uvicorn
        # so a single launch ('tray.bat') brings everything up. Hostname is
        # read from webapp/cloudflared.yml; missing config skips the tunnel.
        self.tunnel_hostname: Optional[str] = read_tunnel_hostname(tunnel_config_path)
        # Model label is queried by pystray on every menu draw; cache so the
        # TCP probe + psutil lookup don't fire on each open.
        self._model_label_cache: tuple[float, str] = (0.0, "🧠 model: ?")

    # ------------------------------------------------------------- lifecycle

    def start_all(self) -> None:
        """Kick off whichever subsystems are configured, each on its own
        background thread so a slow/failed one doesn't block the others."""
        if not self.server.status().running:
            threading.Thread(target=self.start_server, daemon=True).start()
        if self.webapp.config.enabled:
            threading.Thread(target=self.start_webapp, daemon=True).start()
        if self.tunnel_hostname is not None:
            threading.Thread(target=self.start_tunnel, daemon=True).start()

    def stop_all(self) -> None:
        """Shutdown ordering: tunnel first so the public URL goes 5xx
        immediately while the rest of the cleanup runs, then webapp, then
        whisper-server."""
        self.stop_tunnel()
        try:
            webapp_status = self.webapp.status()
            if webapp_status.ownership == OWNERSHIP_OURS:
                self.webapp.stop()
        except Exception as exc:
            logger.debug(f"webapp shutdown failed: {exc}")
        status = self.server.status()
        if status.running and status.ownership == OWNERSHIP_OURS:
            self._notify("Whisper server", "🛑 Stopped")
            # winotify dispatches the toast via a powershell subprocess; give
            # it a moment to spawn before os._exit(0) tears everything down.
            time.sleep(0.5)
            self.server.stop()

    # ------------------------------------------------------------- server

    def start_server(self) -> None:
        try:
            self.server.start()
            self._notify("Whisper server", "✅ Running")
        except RuntimeError as e:
            self._notify("Server failed to start", str(e))

    def stop_server(self) -> None:
        self.server.stop()

    def model_label(self) -> str:
        now = time.monotonic()
        cached_at, label = self._model_label_cache
        if now - cached_at < self._MODEL_LABEL_TTL:
            return label
        try:
            desc = self.server.describe()
            label = f"🧠 {desc.model_display_name}"
        except Exception:
            label = "🧠 model: ?"
        self._model_label_cache = (now, label)
        return label

    # ------------------------------------------------------------- webapp

    def start_webapp(self) -> None:
        try:
            self.webapp.start()
            logger.info(f"🌐 Webapp ready at {self.webapp.base_url}")
        except RuntimeError as exc:
            logger.warning(f"⚠️  Webapp failed to start: {exc}")
            self._notify("Webapp failed to start", str(exc))

    def restart_webapp(self) -> None:
        try:
            self.webapp.stop()
            self.webapp.start()
            self._notify("Webapp", f"✅ Restarted at {self.webapp.base_url}")
        except RuntimeError as exc:
            self._notify("Webapp restart failed", str(exc))

    def webapp_label(self) -> str:
        try:
            status = self.webapp.status()
            if status.running:
                return f"🌐 webapp :{status.port}"
            return "🌐 webapp: down"
        except Exception:
            return "🌐 webapp: ?"

    # ------------------------------------------------------------- tunnel

    def start_tunnel(self) -> None:
        """Spawn cloudflared against webapp/cloudflared.yml so the
        persistent public URL comes up alongside everything else.
        Best-effort — a missing cloudflared binary or a failed launch
        is logged but doesn't take the tray down."""
        refusal = publish_refusal_reason(current_auth_token())
        if refusal is not None:
            logger.warning(f"⚠️  Cloudflare tunnel not started: {refusal}")
            self._notify("Cloudflare tunnel", f"Not started — {refusal}")
            return
        try:
            self._cloudflared_proc = spawn_cloudflared(self._tunnel_config_path, self._project_root)
        except CloudflaredNotFoundError:
            logger.warning(
                "⚠️  cloudflared not on PATH — public URL won't be reachable. "
                "Install: winget install Cloudflare.cloudflared"
            )
            self._notify(
                "Cloudflare tunnel",
                "cloudflared not on PATH — install via winget",
            )
            return
        except OSError as exc:
            logger.warning(f"⚠️  cloudflared failed to launch: {exc}")
            self._notify("Cloudflare tunnel", f"Failed to start: {exc}")
            return
        logger.info(
            f"🌍 Cloudflare tunnel started → https://{self.tunnel_hostname} "
            f"(pid={self._cloudflared_proc.pid})"
        )
        if self.tunnel_hostname is not None:
            persist_tunnel_url(self.tunnel_hostname, self._tunnel_url_file)

    def stop_tunnel(self) -> None:
        proc = self._cloudflared_proc
        if proc is None:
            return
        self._cloudflared_proc = None
        stop_popen(proc, name="cloudflared")
        remove_tunnel_url_file(self._tunnel_url_file)
