"""Webapp-specific configuration loader.

Lives separately from `app_config.py` because these settings are
authored from the web UI ("Set as default" buttons) and persist across
runs. The tk GUI also reads this file for its polish dropdown so both
surfaces share one source of truth.
"""

from __future__ import annotations

# Standard library imports
import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "webapp_config.json"
)
SAMPLE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "webapp_config.sample.json"
)

DEFAULT_POLISH_MODEL = "qwen3.5-9b"
DEFAULT_POLISH_MODELS = (
    "qwen3.5-9b",
    "gemma4-e4b-it",
    "gemma4-26b-a4b-it",
    "claude-haiku-4-5",
)
DEFAULT_POLISH_PROMPT_ID = "filler-words"
DEFAULT_LLM_HUB_URL = "http://127.0.0.1:8000"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8443
DEFAULT_RETENTION_DAYS = 30
DEFAULT_SILENCE_DBFS_THRESHOLD = -50.0


@dataclass
class WebappConfig:
    """User-authored, persisted webapp settings."""

    polish_model_default: str = DEFAULT_POLISH_MODEL
    polish_models_available: List[str] = field(
        default_factory=lambda: list(DEFAULT_POLISH_MODELS)
    )
    polish_prompt_default: str = DEFAULT_POLISH_PROMPT_ID
    llm_hub_url: str = DEFAULT_LLM_HUB_URL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    history_retention_days: int = DEFAULT_RETENTION_DAYS
    force_builtin_mic_default: bool = False
    preferred_mic_id: Optional[str] = None
    # Bearer token enforced when the request did NOT come from a
    # loopback / tailnet IP. Empty string disables enforcement entirely.
    auth_token: str = ""
    # Optional password gate that hands the bearer token back to the
    # browser when the user types it correctly. Lets a fresh device
    # bootstrap without copy-pasting a tokenised URL — handy on iOS
    # PWAs whose localStorage may be partitioned from Safari's. Empty
    # string disables the password prompt (token-only auth).
    auth_password: str = ""
    # RMS gate before whisper. Clips quieter than this (dBFS) skip the
    # transcription step entirely so whisper can't hallucinate on silence.
    silence_dbfs_threshold: float = DEFAULT_SILENCE_DBFS_THRESHOLD


def load_webapp_config(path: Optional[Path] = None) -> WebappConfig:
    """Load the webapp config, falling back to defaults if the file is missing.

    A missing file is not an error — first-run is expected. The webapp
    creates the file on the first "Set as default" tap.
    """
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        logger.info(
            f"📂 webapp_config not found at {target}, using defaults "
            f"(file will be created when settings change)"
        )
        return WebappConfig()

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            f"⚠️  Could not read {target} ({exc}); falling back to defaults"
        )
        return WebappConfig()

    cfg = WebappConfig(
        polish_model_default=str(
            raw.get("polish_model_default", DEFAULT_POLISH_MODEL)
        ),
        polish_models_available=list(
            raw.get("polish_models_available") or DEFAULT_POLISH_MODELS
        ),
        polish_prompt_default=str(
            raw.get("polish_prompt_default", DEFAULT_POLISH_PROMPT_ID)
        ),
        llm_hub_url=str(raw.get("llm_hub_url", DEFAULT_LLM_HUB_URL)),
        host=str(raw.get("host", DEFAULT_HOST)),
        port=int(raw.get("port", DEFAULT_PORT)),
        history_retention_days=int(
            raw.get("history_retention_days", DEFAULT_RETENTION_DAYS)
        ),
        force_builtin_mic_default=bool(
            raw.get("force_builtin_mic_default", False)
        ),
        preferred_mic_id=raw.get("preferred_mic_id") or None,
        auth_token=str(raw.get("auth_token", "")),
        auth_password=str(raw.get("auth_password", "")),
        silence_dbfs_threshold=float(
            raw.get("silence_dbfs_threshold", DEFAULT_SILENCE_DBFS_THRESHOLD)
        ),
    )
    _validate(cfg)
    return cfg


def save_webapp_config(cfg: WebappConfig, path: Optional[Path] = None) -> Path:
    """Atomically write the config back to disk."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "polish_model_default": cfg.polish_model_default,
        "polish_models_available": list(cfg.polish_models_available),
        "polish_prompt_default": cfg.polish_prompt_default,
        "llm_hub_url": cfg.llm_hub_url,
        "host": cfg.host,
        "port": cfg.port,
        "history_retention_days": cfg.history_retention_days,
        "force_builtin_mic_default": cfg.force_builtin_mic_default,
        "preferred_mic_id": cfg.preferred_mic_id,
        "auth_token": cfg.auth_token,
        "auth_password": cfg.auth_password,
        "silence_dbfs_threshold": cfg.silence_dbfs_threshold,
    }

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    logger.info(f"💾 Saved webapp_config to {target}")
    return target


def update_webapp_config(**fields) -> WebappConfig:
    """Read, patch, save — convenience for the API endpoint."""
    current = load_webapp_config()
    patched = replace(current, **fields)
    _validate(patched)
    save_webapp_config(patched)
    return patched


def append_auth_token(url: str, token: Optional[str]) -> str:
    """Return ``url`` with ``?token=<token>`` appended when ``token`` is set.

    Used by the tray's "Copy mobile URL" and the cloudflared launcher so a
    phone opening the link bootstraps its localStorage on the first visit
    without the user typing anything. Falls through unchanged when the
    token is empty (the auth gate is then disabled server-side anyway).
    """
    if not token:
        return url
    parsed = urlparse(url)
    existing = parsed.query
    extra = urlencode({"token": token})
    new_query = f"{existing}&{extra}" if existing else extra
    return urlunparse(parsed._replace(query=new_query))


def _validate(cfg: WebappConfig) -> None:
    if cfg.polish_model_default not in cfg.polish_models_available:
        raise ValueError(
            f"polish_model_default {cfg.polish_model_default!r} not in "
            f"polish_models_available {cfg.polish_models_available!r}"
        )
    if cfg.history_retention_days < 1:
        raise ValueError("history_retention_days must be >= 1")
    if not (1 <= cfg.port <= 65535):
        raise ValueError(f"port out of range: {cfg.port}")
