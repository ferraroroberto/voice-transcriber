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
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

from .gain import DEFAULT_GAIN_BOOST_DB
from .polish_prompts import DEFAULT_PROMPT_ID as DEFAULT_POLISH_PROMPT_ID
from .silence import DEFAULT_SILENCE_DBFS as DEFAULT_SILENCE_DBFS_THRESHOLD

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
#
# DEFAULT_POLISH_PROMPT_ID, DEFAULT_SILENCE_DBFS_THRESHOLD and
# DEFAULT_GAIN_BOOST_DB are re-exported (imported above) from their owning
# domain modules — polish_prompts.py, silence.py, gain.py — rather than
# re-declared here, so each literal has exactly one source.
DEFAULT_LLM_HUB_URL = "http://127.0.0.1:8000"
DEFAULT_RETENTION_DAYS = 30
# Quiet-environment gain boost — amplifies captured audio before whisper,
# orthogonal to the silence gate above (see src/gain.py). Off by default;
# mirrors the vad_auto_stop_enabled + auto_stop_silence_ms enable/value pair.
DEFAULT_GAIN_BOOST_ENABLED = False
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


# Fields whose raw JSON value is treated as "absent" when falsy (empty
# string / empty list), not just when the key is missing entirely — the
# polish-model picks and the mic pick fall back to their computed default
# even when a stale/empty value was persisted.
_OR_ABSENT_FIELDS = frozenset(
    {"polish_model_default", "polish_models_available", "preferred_mic_id"}
)


def _caster_for(default: Any) -> Callable[[Any], Any]:
    """Infer a JSON-value coercion from a field's default value's type."""
    if default is None:
        return lambda v: v
    py_type = type(default)
    return list if py_type is list else py_type


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

    # One field list (dataclasses.fields()), not three: each field's own
    # declared default supplies both the fallback value and — via its
    # Python type — the JSON coercion, so a new knob needs no reader edit.
    defaults = WebappConfig()
    kwargs: Dict[str, Any] = {}
    for f in fields(WebappConfig):
        default = getattr(defaults, f.name)
        if f.name in _OR_ABSENT_FIELDS:
            raw_value = raw.get(f.name) or default
        else:
            raw_value = raw.get(f.name, default)
        kwargs[f.name] = _caster_for(default)(raw_value)
    cfg = WebappConfig(**kwargs)
    _validate(cfg)
    return cfg


def _field_value(cfg: WebappConfig, name: str) -> Any:
    value = getattr(cfg, name)
    return list(value) if isinstance(value, list) else value


def save_webapp_config(cfg: WebappConfig, path: Optional[Path] = None) -> Path:
    """Atomically write the config back to disk."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {f.name: _field_value(cfg, f.name) for f in fields(WebappConfig)}

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


# Fields never sent to the client: secrets (auth_token/auth_password), and
# llm_hub_url/silence_dbfs_threshold which are server-internal (see the
# field comments above on WebappConfig).
CLIENT_HIDDEN_FIELDS = frozenset(
    {"auth_token", "auth_password", "llm_hub_url", "silence_dbfs_threshold"}
)


def config_to_client_dict(cfg: WebappConfig) -> Dict[str, Any]:
    """Serialize the client-facing subset of ``cfg`` for the webapp API.

    Single source for what `GET /api/config` and `POST /api/config` both
    return, so the two responses can never drift out of shape from each
    other again — adding a client-visible knob is a dataclass-field edit,
    not a fifth hand-written dict.
    """
    return {
        f.name: _field_value(cfg, f.name)
        for f in fields(WebappConfig)
        if f.name not in CLIENT_HIDDEN_FIELDS
    }


def _validate(cfg: WebappConfig) -> None:
    if cfg.polish_model_default not in cfg.polish_models_available:
        raise ValueError(
            f"polish_model_default {cfg.polish_model_default!r} not in "
            f"polish_models_available {cfg.polish_models_available!r}"
        )
    if cfg.history_retention_days < 1:
        raise ValueError("history_retention_days must be >= 1")
    if cfg.partial_interval_seconds < 0:
        raise ValueError("partial_interval_seconds must be >= 0 (0 disables)")
    if cfg.auto_stop_silence_ms < 200:
        raise ValueError("auto_stop_silence_ms must be >= 200")
    if not (0.0 <= cfg.gain_boost_db <= 24.0):
        raise ValueError("gain_boost_db must be between 0 and 24")
