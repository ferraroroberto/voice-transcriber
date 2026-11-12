"""Webapp process manager — adopt-or-spawn for the HTTP/2 webapp.

Same lifecycle as `src/whisper_server/manager.py`, both built on the
shared `src/process_supervisor.py` (ownership ladder, spawn recipe, stop
ladder, readiness wait):

- `status()` checks `GET /healthz` and a low-level TCP probe.
- `start()` adopts an already-listening webapp (no second spawn) or
  spawns `python -m app.webapp.serve` from this venv.
- `stop()` only terminates a process this manager spawned. An externally
  started uvicorn is left alone.

Used by the tray (`app/tray/tray.py`) so launching `tray.bat` brings up
the webapp alongside whisper-server. Standalone `webapp.bat` is the
"server only, no tray" alternative.
"""

from __future__ import annotations

# Standard library imports
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Third-party imports
import requests

from app.tray.single_instance import cross_process_lock
from app.webapp.event_loop import LOOP_FACTORY
from src.process_supervisor import (
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    is_port_in_use,
    ownership_of,
    spawn_managed,
    stop_popen,
    wait_until_ready,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class WebappRuntimeConfig:
    """Process-spawn knobs for the uvicorn webapp, read from
    config/config.json's `webapp` section.

    Distinct from `src.webapp_config.WebappConfig`, which holds the
    user-authored, persisted settings (polish models, auth, retention).
    This is the authoritative source of the bind `host`/`port` the tray
    spawns uvicorn on; the `host`/`port` fields on the persisted config
    are not used for binding.
    """
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8443
    startup_timeout_seconds: float = 15.0
    request_timeout_seconds: float = 1.0
    # hypercorn serves HTTP/2 over the certificate; uvicorn is the HTTP/1.1 fallback.
    server: str = "hypercorn"
    poll_interval_seconds: float = 0.4


@dataclass
class WebappStatus:
    running: bool
    ownership: str
    pid: Optional[int]
    port: int
    base_url: str  # https://… when cert exists, http://… otherwise
    detail: str


def load_config(raw: Optional[Dict[str, Any]] = None) -> WebappRuntimeConfig:
    """Build a WebappRuntimeConfig from the optional `webapp` section of config.json."""
    raw = raw or {}
    return WebappRuntimeConfig(
        enabled=bool(raw.get("enabled", True)),
        host=str(raw.get("host", "0.0.0.0")),
        port=int(raw.get("port", 8443)),
        server=str(raw.get("server", "hypercorn")),
    )


def cert_paths(project_root: Optional[Path] = None) -> Optional[tuple[Path, Path]]:
    """Return (cert.pem, key.pem) if both exist, else None."""
    root = project_root or PROJECT_ROOT
    cert = root / "webapp" / "certificates" / "cert.pem"
    key = root / "webapp" / "certificates" / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    return None


def _probe_url(scheme: str, host: str, port: int) -> str:
    return f"{scheme}://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"


def build_webapp_command(
    host: str,
    port: int,
    certs: Optional[tuple[Path, Path]] = None,
    server: str = "hypercorn",
) -> List[str]:
    """Build the ``-m app.webapp.serve ...`` argv (everything after the python
    executable) for spawning the webapp.

    Shared by :class:`WebappManager` (the tray-managed webapp) and
    ``scripts/run_named_tunnel.py`` (the headless named-tunnel launcher)
    so a flag added on one spawn path is never missing from the other
    — each caller prepends its own resolved python executable.
    """
    cmd: List[str] = [
        "-m",
        "app.webapp.serve",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--loop",
        LOOP_FACTORY,
        # serve.py wraps the app so only a loopback peer (the tray's
        # cloudflared, webapp/cloudflared.yml -> https://localhost:8443) may
        # set X-Forwarded-For; request.client.host then reflects the real
        # tunnel caller, not 127.0.0.1.
        "--server",
        server,
    ]
    if certs is not None:
        cert, key = certs
        cmd.extend([
            "--ssl-keyfile",
            str(key),
            "--ssl-certfile",
            str(cert),
        ])
    return cmd


class WebappManager:
    """Start / stop / health-check the webapp process."""

    def __init__(self, config: Optional[WebappRuntimeConfig] = None) -> None:
        self.config = config or WebappRuntimeConfig()
        self._proc: Optional[subprocess.Popen] = None
        self._session = requests.Session()
        self._session.verify = False  # self-signed cert in HTTPS mode
        # Suppress requests' "InsecureRequestWarning" for the self-signed loopback probe.
        try:
            import urllib3
            from urllib3.exceptions import InsecureRequestWarning
            urllib3.disable_warnings(InsecureRequestWarning)
        except Exception:
            pass

    # ------------------------------------------------------------- properties

    @property
    def base_url(self) -> str:
        scheme = "https" if cert_paths() else "http"
        return _probe_url(scheme, self.config.host, self.config.port)

    # ----------------------------------------------------------------- status

    def is_reachable(self) -> bool:
        for scheme in ("https", "http"):
            url = _probe_url(scheme, self.config.host, self.config.port) + "/healthz"
            try:
                r = self._session.get(url, timeout=self.config.request_timeout_seconds)
                if r.status_code == 200:
                    return True
            except requests.RequestException:
                continue
        return False

    def is_port_in_use(self) -> bool:
        return is_port_in_use(self.config.host, self.config.port)

    def status(self) -> WebappStatus:
        reachable = self.is_reachable() or self.is_port_in_use()
        ownership = ownership_of(self._proc, reachable=reachable)

        if ownership == OWNERSHIP_OURS:
            return WebappStatus(
                running=True,
                ownership=ownership,
                pid=self._proc.pid,
                port=self.config.port,
                base_url=self.base_url,
                detail="running (started by this process)",
            )
        if ownership == OWNERSHIP_EXTERNAL:
            return WebappStatus(
                running=True,
                ownership=ownership,
                pid=None,
                port=self.config.port,
                base_url=self.base_url,
                detail="running (external — adopted)",
            )
        return WebappStatus(
            running=False,
            ownership=ownership,
            pid=None,
            port=self.config.port,
            base_url=self.base_url,
            detail="not running",
        )

    # ------------------------------------------------------------------ start

    def start(self, wait: bool = True) -> WebappStatus:
        """Adopt-or-spawn. Idempotent — returns current status if already up."""
        if not self.config.enabled:
            logger.info("ℹ️  Webapp is disabled in config (webapp.enabled=false)")
            return self.status()

        # Race-safe adopt-or-spawn: serialize the
        # status()-then-Popen critical section across processes so two trays
        # starting at once cannot both spawn uvicorn. The loser blocks, then
        # re-checks below and adopts the now-listening webapp. The lock is held
        # through _wait_until_ready so a serialized caller sees a bound port.
        # cross_process_lock fails open (Windows mutex glitch / non-Windows), so
        # it never blocks startup. Vendored byte-identical from the scaffold.
        with cross_process_lock(rf"Global\voice-transcriber-webapp-start-{self.config.port}"):
            current = self.status()
            if current.running and current.ownership == OWNERSHIP_OURS:
                logger.info(f"ℹ️  Webapp already {current.detail}")
                return current
            if current.running:
                logger.info(f"🔗 Adopting external webapp at {current.base_url}")
                return current

            cmd = self._build_command()
            logger.info(f"🚀 Starting webapp: {' '.join(cmd)}")

            self._proc = spawn_managed(
                cmd,
                name="webapp",
                cwd=PROJECT_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                not_found_message=lambda exc: f"❌ python launcher not found: {exc}",
            )

            if wait:
                self._wait_until_ready()
            return self.status()

    # ------------------------------------------------------------------- stop

    def stop(self) -> WebappStatus:
        status = self.status()
        if status.ownership == OWNERSHIP_EXTERNAL:
            logger.info("✋ Leaving external webapp running (not ours)")
            return status
        if not status.running or self._proc is None:
            return status

        p = self._proc
        try:
            stop_popen(p, name="webapp", terminate_timeout=5, kill_timeout=3)
        finally:
            self._proc = None

        return WebappStatus(
            running=False,
            ownership=OWNERSHIP_NONE,
            pid=None,
            port=self.config.port,
            base_url=self.base_url,
            detail="stopped",
        )

    # --------------------------------------------------------------- helpers

    def _build_command(self) -> List[str]:
        py = sys.executable  # the venv that launched the tray
        return [py] + build_webapp_command(
            self.config.host, self.config.port, cert_paths(), self.config.server
        )

    def _wait_until_ready(self) -> None:
        wait_until_ready(
            still_alive=lambda: self._proc is not None and self._proc.poll() is None,
            is_reachable=self.is_reachable,
            timeout_seconds=self.config.startup_timeout_seconds,
            poll_interval_seconds=self.config.poll_interval_seconds,
            not_alive_message=lambda: "❌ webapp uvicorn exited before becoming ready",
            timeout_message=(
                f"❌ webapp did not become ready within "
                f"{self.config.startup_timeout_seconds}s"
            ),
        )
        logger.info(f"✅ Webapp ready at {self.base_url}")
