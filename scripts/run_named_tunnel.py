"""Start uvicorn + cloudflared on a named (persistent) tunnel.

Used by `webapp_tunnel_named.bat` for headless / no-tray use. The
tray (`tray.bat`) already does this same work as part of normal
startup — only reach for this script when running without the tray.

Boots:

  1. uvicorn (HTTPS if `webapp/certificates/cert.pem` exists)
  2. cloudflared tunnel --config webapp/cloudflared.yml run

The persistent URL is written to `webapp/last_tunnel_url.txt` (with
`?token=…` appended when an `auth_token` is configured) so external
tooling can find it.

One-time setup before this script can run — see README →
"Persistent URL via Cloudflare tunnel":

  cloudflared tunnel login
  cloudflared tunnel create voice
  cloudflared tunnel route dns voice voice.<your-domain>
  cp webapp/cloudflared.sample.yml webapp/cloudflared.yml  # then edit
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Run directly as `python scripts/run_named_tunnel.py` (see
# webapp_tunnel_named.bat), so sys.path[0] is this script's own directory,
# not the repo root -- `src` isn't importable without this.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.webapp.manager import build_uvicorn_command, cert_paths  # noqa: E402
from src.process_supervisor import stop_popen  # noqa: E402
from src.tunnel import (  # noqa: E402
    CloudflaredNotFoundError,
    persist_tunnel_url,
    read_tunnel_hostname,
    remove_tunnel_url_file,
    spawn_cloudflared,
)
from _no_window import no_window_kwargs  # noqa: E402
from _utf8 import reconfigure_utf8_streams  # noqa: E402

logger = logging.getLogger("run_named_tunnel")

DEFAULT_CONFIG = PROJECT_ROOT / "webapp" / "cloudflared.yml"
SAMPLE_CONFIG = PROJECT_ROOT / "webapp" / "cloudflared.sample.yml"
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"
DEFAULT_PORT = 8443


def _have_listener(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_python() -> Path:
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return venv_py
    return Path(sys.executable)


def _spawn_uvicorn(port: int) -> subprocess.Popen:
    # Bound to loopback only -- cloudflared connects to this uvicorn over
    # 127.0.0.1, never the tray's config-driven bind host. Command
    # construction beyond host/port/certs is shared with
    # `WebappManager._build_command` via `build_uvicorn_command` so a flag
    # can never be added to one spawn path only (voice-transcriber#160).
    cmd = [str(_find_python())] + build_uvicorn_command(
        "127.0.0.1", port, cert_paths(PROJECT_ROOT)
    )
    logger.info(f"🚀 Starting uvicorn: {' '.join(cmd)}")
    kw: dict = dict(cwd=str(PROJECT_ROOT), **no_window_kwargs())
    if sys.platform == "win32":
        kw["creationflags"] |= subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, **kw)


def _wait_for_uvicorn(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _have_listener(port):
            return True
        time.sleep(0.3)
    return False


def _stream(proc: subprocess.Popen) -> None:
    for line in proc.stdout or ():
        sys.stdout.write(line)
        sys.stdout.flush()


def main() -> int:
    reconfigure_utf8_streams()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(
        os.environ.get("CLOUDFLARED_CONFIG", str(DEFAULT_CONFIG))
    )
    if not config_path.exists():
        logger.error(
            f"❌ {config_path} missing. Copy {SAMPLE_CONFIG.name} to "
            f"{config_path.name} and fill in your tunnel UUID + hostname. "
            "See README → 'Persistent URL via named Cloudflare tunnel'."
        )
        return 1

    hostname = read_tunnel_hostname(config_path)
    if hostname:
        logger.info(f"🌍 Public hostname: https://{hostname}")
    else:
        logger.warning(
            "⚠️  No hostname found in ingress[] — tunnel will still run "
            "but last_tunnel_url.txt won't be updated."
        )

    port = int(os.environ.get("WEBAPP_PORT", DEFAULT_PORT))
    uvicorn_proc: Optional[subprocess.Popen] = None
    if _have_listener(port):
        logger.info(f"🔗 Adopting existing webapp on :{port}")
    else:
        uvicorn_proc = _spawn_uvicorn(port)
        if not _wait_for_uvicorn(port):
            logger.error("❌ uvicorn failed to start within 15 s")
            if uvicorn_proc is not None:
                uvicorn_proc.terminate()
            return 1

    try:
        cloudflared = spawn_cloudflared(config_path, PROJECT_ROOT, capture_output=True)
    except CloudflaredNotFoundError as exc:
        logger.error(f"❌ {exc}")
        if uvicorn_proc is not None:
            uvicorn_proc.terminate()
        return 1
    streamer = threading.Thread(target=_stream, args=(cloudflared,), daemon=True)
    streamer.start()

    if hostname:
        persist_tunnel_url(hostname, TUNNEL_URL_FILE)

    try:
        cloudflared.wait()
    except KeyboardInterrupt:
        logger.info("⏹️  Ctrl+C — shutting down")
    finally:
        for proc, name in ((cloudflared, "cloudflared"), (uvicorn_proc, "uvicorn")):
            if proc is not None:
                stop_popen(proc, name=name)
        remove_tunnel_url_file(TUNNEL_URL_FILE)

    return 0


if __name__ == "__main__":
    sys.exit(main())
