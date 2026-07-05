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

# Polish-model defaults come from the committed sample config, not from
# Python literals — so adding/removing/renaming a hub alias is a single
# JSON edit, no repo push. The sample file is the source of truth for
# first-run defaults; the runtime `webapp_config.json` (gitignored) is
# the user's persisted overrides on top of it.
DEFAULT_POLISH_PROMPT_ID = "filler-words"
DEFAULT_LLM_HUB_URL = "http://127.0.0.1:8000"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8443
DEFAULT_RETENTION_DAYS = 30
DEFAULT_SILENCE_DBFS_THRESHOLD = -50.0
# Quiet-environment gain boost — amplifies captured audio before whisper,
# orthogonal to the silence gate above (see src/gain.py). Off by default;
# mirrors the vad_auto_stop_enabled + auto_stop_silence_ms enable/value pair.
DEFAULT_GAIN_BOOST_ENABLED = False
DEFAULT_GAIN_BOOST_DB = 12.0
# Pillar 1 (rolling transcription): how often to re-run whisper on the
# accumulated take while the user is still recording. 0 disables the
# rolling worker entirely; the webapp falls back to the legacy "one
# whisper pass on /finish" behaviour.
DEFAULT_PARTIAL_INTERVAL_SECONDS = 2.0
# Pillar 3 (VAD-driven auto-stop): off by default. When on, the client
# watches its own AnalyserNode energy floor and fires Stop after
# ``auto_stop_silence_ms`` of continuous near-silence. A grace banner on
# the page lets the user keep talking to cancel.
DEFAULT_VAD_AUTO_STOP_ENABLED = False
DEFAULT_AUTO_STOP_SILENCE_MS = 1500


def _sample_polish_defaults() -> tuple[str, List[str]]:
    """Read the committed sample config to get the first-run polish-model
    defaults. Keeps Python free of model-name literals so the list can
    evolve in JSON alone."""
    try:
        raw = json.loads(SAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            f"⚠️  Could not read sample config {SAMPLE_CONFIG_PATH} "
            f"({exc}); polish defaults will be empty"
        )
        return "", []
    return (
        str(raw.get("polish_model_default") or ""),
        list(raw.get("polish_models_available") or []),
    )


@dataclass
class WebappConfig:
    """User-authored, persisted webapp settings."""

    polish_model_default: str = field(
        default_factory=lambda: _sample_polish_defaults()[0]
    )
    polish_models_available: List[str] = field(
        default_factory=lambda: _sample_polish_defaults()[1]
    )
    polish_prompt_default: str = DEFAULT_POLISH_PROMPT_ID
    llm_hub_url: str = DEFAULT_LLM_HUB_URL
    # Persisted for reference/UI only — the authoritative bind host/port
    # the tray spawns uvicorn on lives in config/config.json's `webapp`
    # section (see app.webapp.manager.WebappRuntimeConfig). These are not
    # read for binding.
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
    # Quiet-environment gain boost — applied to audio after the silence
    # gate passes, before it reaches whisper. See src/gain.py.
    gain_boost_enabled: bool = DEFAULT_GAIN_BOOST_ENABLED
    gain_boost_db: float = DEFAULT_GAIN_BOOST_DB
    # Latency-collapse knobs — see DEFAULT_* constants above for the
    # rationale and the per-pillar on/off defaults.
    partial_interval_seconds: float = DEFAULT_PARTIAL_INTERVAL_SECONDS
    vad_auto_stop_enabled: bool = DEFAULT_VAD_AUTO_STOP_ENABLED
    auto_stop_silence_ms: int = DEFAULT_AUTO_STOP_SILENCE_MS


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

    sample_default, sample_available = _sample_polish_defaults()
    cfg = WebappConfig(
        polish_model_default=str(
            raw.get("polish_model_default") or sample_default
        ),
        polish_models_available=list(
            raw.get("polish_models_available") or sample_available
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
        gain_boost_enabled=bool(
            raw.get("gain_boost_enabled", DEFAULT_GAIN_BOOST_ENABLED)
        ),
        gain_boost_db=float(raw.get("gain_boost_db", DEFAULT_GAIN_BOOST_DB)),
        partial_interval_seconds=float(
            raw.get("partial_interval_seconds", DEFAULT_PARTIAL_INTERVAL_SECONDS)
        ),
        vad_auto_stop_enabled=bool(
            raw.get("vad_auto_stop_enabled", DEFAULT_VAD_AUTO_STOP_ENABLED)
        ),
        auto_stop_silence_ms=int(
            raw.get("auto_stop_silence_ms", DEFAULT_AUTO_STOP_SILENCE_MS)
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
        "gain_boost_enabled": cfg.gain_boost_enabled,
        "gain_boost_db": cfg.gain_boost_db,
        "partial_interval_seconds": cfg.partial_interval_seconds,
        "vad_auto_stop_enabled": cfg.vad_auto_stop_enabled,
        "auto_stop_silence_ms": cfg.auto_stop_silence_ms,
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

    Used by the tray's "Copy local URL" / "Copy Cloudflare URL" and the cloudflared launcher so a
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
    if cfg.partial_interval_seconds < 0:
        raise ValueError("partial_interval_seconds must be >= 0 (0 disables)")
    if cfg.auto_stop_silence_ms < 200:
        raise ValueError("auto_stop_silence_ms must be >= 200")
    if not (0.0 <= cfg.gain_boost_db <= 24.0):
        raise ValueError("gain_boost_db must be between 0 and 24")
