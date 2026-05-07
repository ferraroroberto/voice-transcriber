"""Start uvicorn + cloudflared and capture the trycloudflare.com URL.

Used by `webapp_tunnel.bat` for the work scenario where Tailscale isn't
available. Boots:

  1. uvicorn (HTTPS if `webapp/certificates/cert.pem` exists)
  2. cloudflared tunnel --url http://localhost:<port>

Watches cloudflared's stdout/stderr for the generated
`https://*.trycloudflare.com` URL and writes it to
`webapp/last_tunnel_url.txt` so the tray (or a separate launcher) can
surface it. Streams cloudflared output to the console live.

Press Ctrl+C to stop both processes cleanly.

When `auth_token` is set in `config/webapp_config.json`, the URL we
persist already includes `?token=…` so the phone bootstraps its
localStorage on the first visit. Token enforcement is performed
server-side by `app/webapp/server.py`. Run `scripts/gen_token.py` to
generate / rotate the token.
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("run_tunnel")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"
DEFAULT_PORT = 8443

URL_PATTERN = re.compile(
    r"https://[a-z0-9-]+\.trycloudflare\.com",
    re.IGNORECASE,
)


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
    cert = PROJECT_ROOT / "webapp" / "certificates" / "cert.pem"
    key = PROJECT_ROOT / "webapp" / "certificates" / "key.pem"
    cmd = [
        str(_find_python()),
        "-m",
        "uvicorn",
        "app.webapp.server:app",
        "--host",
        "127.0.0.1",  # cloudflared connects to loopback only
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    if cert.exists() and key.exists():
        cmd.extend(["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)])

    logger.info(f"🚀 Starting uvicorn: {' '.join(cmd)}")
    kw: dict = dict(cwd=str(PROJECT_ROOT))
    if sys.platform == "win32":
        kw["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kw)


def _spawn_cloudflared(port: int) -> subprocess.Popen:
    bin_path = shutil.which("cloudflared")
    if bin_path is None:
        raise SystemExit(
            "❌ cloudflared not found on PATH. Install: "
            "winget install Cloudflare.cloudflared"
        )
    # cloudflared expects HTTP locally — TLS termination is at Cloudflare's edge.
    # Even if uvicorn is HTTPS, cloudflared can still terminate against it but
    # we'd need --http2-origin --no-tls-verify. Easier: keep the local hop
    # plain and let Cloudflare provide HTTPS. So if HTTPS uvicorn, we still
    # tell cloudflared the origin is `https://localhost:port` with --no-tls-verify.
    cert = PROJECT_ROOT / "webapp" / "certificates" / "cert.pem"
    if cert.exists():
        origin = f"https://localhost:{port}"
        extra = ["--no-tls-verify"]
    else:
        origin = f"http://localhost:{port}"
        extra = []
    cmd = [bin_path, "tunnel", "--url", origin, *extra]
    logger.info(f"🌐 Starting cloudflared: {' '.join(cmd)}")
    return subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def _read_auth_token() -> str:
    """Best-effort read of the bearer token without importing heavy deps."""
    try:
        from src.webapp_config import load_webapp_config  # local import — keep startup light
        return (load_webapp_config().auth_token or "").strip()
    except Exception as exc:
        logger.debug(f"could not read auth_token: {exc}")
        return ""


def _stream_and_capture_url(proc: subprocess.Popen) -> None:
    """Tail cloudflared's stdout, echo to console, persist any URL match."""
    captured: str | None = None
    for line in proc.stdout or ():
        sys.stdout.write(line)
        sys.stdout.flush()
        if captured:
            continue
        match = URL_PATTERN.search(line)
        if match:
            captured = match.group(0)
            token = _read_auth_token()
            if token:
                from src.webapp_config import append_auth_token
                persisted = append_auth_token(captured, token)
            else:
                persisted = captured
            try:
                TUNNEL_URL_FILE.parent.mkdir(parents=True, exist_ok=True)
                TUNNEL_URL_FILE.write_text(persisted + "\n", encoding="utf-8")
                logger.info(f"📡 Tunnel URL → {TUNNEL_URL_FILE}")
                logger.info(f"   {persisted}")
                if token:
                    logger.info(
                        "🔐 auth_token is set — the URL above includes "
                        "?token=… so the phone bootstraps on first load."
                    )
            except OSError as exc:
                logger.warning(f"⚠️  Could not write {TUNNEL_URL_FILE}: {exc}")


def _wait_for_uvicorn(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _have_listener(port):
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    port = int(os.environ.get("WEBAPP_PORT", DEFAULT_PORT))

    # Adopt an already-running uvicorn rather than spawning a duplicate.
    uvicorn_proc: subprocess.Popen | None = None
    if _have_listener(port):
        logger.info(f"🔗 Adopting existing webapp on :{port}")
    else:
        uvicorn_proc = _spawn_uvicorn(port)
        if not _wait_for_uvicorn(port):
            logger.error("❌ uvicorn failed to start within 15 s")
            if uvicorn_proc is not None:
                uvicorn_proc.terminate()
            return 1

    cloudflared = _spawn_cloudflared(port)
    streamer = threading.Thread(
        target=_stream_and_capture_url, args=(cloudflared,), daemon=True,
    )
    streamer.start()

    try:
        cloudflared.wait()
    except KeyboardInterrupt:
        logger.info("⏹️  Ctrl+C — shutting down")
    finally:
        for proc, name in ((cloudflared, "cloudflared"), (uvicorn_proc, "uvicorn")):
            if proc is None:
                continue
            try:
                logger.info(f"🛑 Stopping {name} (pid={proc.pid})")
                if sys.platform == "win32":
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:
                logger.debug(f"{name} stop failed: {exc}")
        # Don't leave a stale URL pointing at a closed tunnel.
        try:
            if TUNNEL_URL_FILE.exists():
                TUNNEL_URL_FILE.unlink()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
