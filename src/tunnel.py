"""Shared Cloudflare named-tunnel lifecycle: read the configured hostname,
spawn ``cloudflared``, and persist the public URL.

Both the tray (``app/gui/tray.py``, auto-spawned as part of normal
``tray.bat`` startup) and the headless ``scripts/run_named_tunnel.py``
front the same webapp through the same ``cloudflared tunnel --config …
run`` process and the same ``webapp/last_tunnel_url.txt`` persistence
(with ``?token=…`` appended when an ``auth_token`` is configured) — this
module is the one place that lives so a fix lands once instead of twice.
Teardown of the spawned process goes through
``src.process_supervisor.stop_popen``, the single owner of the
CTRL_BREAK/terminate/kill ladder (voice-transcriber#160).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

from src.webapp_config import append_auth_token, load_webapp_config

logger = logging.getLogger(__name__)


class CloudflaredNotFoundError(RuntimeError):
    """``cloudflared`` isn't on PATH."""


def read_tunnel_hostname(config_path: Path) -> Optional[str]:
    """Pull the first ``ingress[].hostname`` out of a cloudflared config.

    Returns ``None`` when the file is missing or unparseable — callers
    treat either case as "no tunnel" and skip spawning cloudflared.
    """
    if not config_path.exists():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"⚠️  Could not parse {config_path}: {exc}")
        return None
    for entry in data.get("ingress") or []:
        if isinstance(entry, dict) and entry.get("hostname"):
            return str(entry["hostname"]).strip()
    return None


def publish_refusal_reason(auth_token: str) -> Optional[str]:
    """Return why the tunnel must not be published, or ``None`` to proceed.

    The bearer middleware treats an empty ``auth_token`` as "gate off" and
    lets every caller through. That is the right default for a loopback-only
    app, but publishing the same origin on a stable public hostname turns it
    into an open one — so the two settings have to be decided together rather
    than independently. Callers refuse the spawn and surface the reason.
    """
    if not (auth_token or "").strip():
        return (
            "no auth_token configured — refusing to publish the webapp on a "
            "public hostname without one. Run scripts/gen_token.py, then "
            "restart the tray."
        )
    return None


def spawn_cloudflared(
    config_path: Path, cwd: Path, *, capture_output: bool = False
) -> subprocess.Popen:
    """Start ``cloudflared tunnel --config <config_path> run``.

    Raises ``CloudflaredNotFoundError`` when the binary isn't on PATH.
    ``capture_output=True`` (the headless script, which streams the log to
    its own stdout) pipes stdout+stderr merged as text; the tray's
    fire-and-forget use leaves them on ``DEVNULL``.
    """
    bin_path = shutil.which("cloudflared")
    if bin_path is None:
        raise CloudflaredNotFoundError(
            "cloudflared not found on PATH. Install: "
            "winget install Cloudflare.cloudflared"
        )
    cmd = [bin_path, "tunnel", "--config", str(config_path), "run"]
    logger.info(f"🌐 Starting cloudflared: {' '.join(cmd)}")
    kw: dict = dict(cwd=str(cwd))
    if capture_output:
        kw.update(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    else:
        kw.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == "win32":
        kw["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kw)


def persist_tunnel_url(hostname: str, url_file: Path) -> None:
    """Write the public URL (with ``?token=…`` when configured) to
    ``url_file`` so external tooling (the launcher hub) can find it."""
    url = f"https://{hostname}"
    try:
        token = (load_webapp_config().auth_token or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"could not read auth_token: {exc}")
        token = ""
    if token:
        url = append_auth_token(url, token)
    try:
        url_file.parent.mkdir(parents=True, exist_ok=True)
        url_file.write_text(url + "\n", encoding="utf-8")
        logger.info(f"📡 Tunnel URL → {url_file}")
        logger.info(f"   {url}")
        if token:
            logger.info(
                "🔐 auth_token is set — the URL above includes ?token=… so "
                "the phone bootstraps on first load."
            )
    except OSError as exc:
        logger.warning(f"⚠️  Could not write {url_file}: {exc}")


def remove_tunnel_url_file(url_file: Path) -> None:
    """Delete the persisted URL file, if any — so a dead tunnel doesn't
    leave a stale URL for external tooling to pick up."""
    try:
        if url_file.exists():
            url_file.unlink()
    except OSError:
        pass
