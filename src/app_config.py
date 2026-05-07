"""Application-level configuration loader (separate from server config)."""

from __future__ import annotations

# Standard library imports
import json
import logging
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Supported dictation modes. The value is the whisper-server `language` hint.
LANGUAGE_MODES: Tuple[str, ...] = ("english", "spanish")

_MODE_TO_ISO: Dict[str, str] = {
    "english": "en",
    "spanish": "es",
}

LANGUAGE_MODE_LABELS: Dict[str, str] = {
    "english": "English",
    "spanish": "Spanish",
}

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class AppConfig:
    language: str = "english"
    max_record_seconds: int = 300
    sample_rate: int = 16000
    preferred_mics: Optional[List[str]] = None
    machine_specific_mics: Dict[str, List[str]] = field(default_factory=dict)
    hotkey: str = "<ctrl>+<alt>+<space>"
    auto_copy: bool = True
    auto_start_server: bool = False
    log_level: str = "INFO"
    # Optional webapp section — when missing, the tray spawns the webapp
    # on `:8443` with default settings. Set webapp.enabled:false to opt out.
    webapp: Dict = field(default_factory=dict)

    @property
    def whisper_language(self) -> str:
        """ISO language code for the configured dictation mode."""
        return _MODE_TO_ISO[self.language]

    @property
    def hotkey_label(self) -> str:
        """Human-readable form of ``hotkey`` (e.g. ``<f10>`` → ``F10``)."""
        parts = []
        for token in self.hotkey.split("+"):
            token = token.strip().lstrip("<").rstrip(">")
            if not token:
                continue
            parts.append(token.capitalize() if len(token) > 1 else token.upper())
        return "+".join(parts)

    def resolve_preferred_mics(self) -> List[str]:
        """Pick the mic list for the current machine (explicit > machine-map > [])."""
        if self.preferred_mics:
            return self.preferred_mics
        machine = _machine_name()
        mapped = self.machine_specific_mics.get(machine)
        if mapped:
            logger.info(f"🎙️  Using machine-specific mics for '{machine}': {mapped}")
            return mapped
        logger.info(f"🎙️  No mic preferences for '{machine}' — will use the system default")
        return []


def _machine_name() -> str:
    try:
        return platform.node().lower()
    except Exception:
        return "unknown"


def load_app_config(path: Optional[Path] = None) -> AppConfig:
    """Load `config/config.json` from next to this file (or an override)."""
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    else:
        path = Path(path).resolve()

    if not path.exists():
        logger.warning(f"📂 Config not found at {path}, using defaults")
        return AppConfig()

    raw = json.loads(path.read_text(encoding="utf-8"))
    _validate(raw)
    return AppConfig(
        language=raw.get("language", "english"),
        max_record_seconds=int(raw.get("max_record_seconds", 300)),
        sample_rate=int(raw.get("sample_rate", 16000)),
        preferred_mics=raw.get("preferred_mics") or None,
        machine_specific_mics=raw.get("machine_specific_mics") or {},
        hotkey=raw.get("hotkey", "<ctrl>+<alt>+<space>"),
        auto_copy=bool(raw.get("auto_copy", True)),
        auto_start_server=bool(raw.get("auto_start_server", False)),
        log_level=raw.get("log_level", "INFO"),
        webapp=raw.get("webapp") or {},
    )


def _validate(raw: Dict) -> None:
    if "language" in raw and raw["language"] not in LANGUAGE_MODES:
        raise ValueError(f"language must be one of {LANGUAGE_MODES}")
    if "log_level" in raw and raw["log_level"] not in VALID_LOG_LEVELS:
        raise ValueError(f"log_level must be one of {VALID_LOG_LEVELS}")
    if "max_record_seconds" in raw:
        v = raw["max_record_seconds"]
        if not isinstance(v, int) or v <= 0:
            raise ValueError("max_record_seconds must be a positive integer")
