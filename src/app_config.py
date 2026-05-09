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

# Full Whisper language map (99 entries) — ISO 639-1 (or 3-letter for haw/yue)
# keyed to a human-readable label. Mirrors whisper.cpp's `g_lang` table so any
# code Whisper accepts is in the picker. Order is alphabetical-by-label
# at module top so dropdowns render naturally; lookups by ISO are O(1) via
# the dict regardless.
WHISPER_LANGUAGES: Dict[str, str] = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic",
    "hy": "Armenian", "as": "Assamese", "az": "Azerbaijani", "ba": "Bashkir",
    "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian",
    "br": "Breton", "bg": "Bulgarian", "my": "Burmese", "yue": "Cantonese",
    "ca": "Catalan", "zh": "Chinese", "hr": "Croatian", "cs": "Czech",
    "da": "Danish", "nl": "Dutch", "en": "English", "et": "Estonian",
    "fo": "Faroese", "fi": "Finnish", "fr": "French", "gl": "Galician",
    "ka": "Georgian", "de": "German", "el": "Greek", "gu": "Gujarati",
    "ht": "Haitian Creole", "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew",
    "hi": "Hindi", "hu": "Hungarian", "is": "Icelandic", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "jw": "Javanese", "kn": "Kannada",
    "kk": "Kazakh", "km": "Khmer", "ko": "Korean", "lo": "Lao",
    "la": "Latin", "lv": "Latvian", "ln": "Lingala", "lt": "Lithuanian",
    "lb": "Luxembourgish", "mk": "Macedonian", "mg": "Malagasy", "ms": "Malay",
    "ml": "Malayalam", "mt": "Maltese", "mi": "Maori", "mr": "Marathi",
    "mn": "Mongolian", "ne": "Nepali", "no": "Norwegian", "nn": "Nynorsk",
    "oc": "Occitan", "ps": "Pashto", "fa": "Persian", "pl": "Polish",
    "pt": "Portuguese", "pa": "Punjabi", "ro": "Romanian", "ru": "Russian",
    "sa": "Sanskrit", "sr": "Serbian", "sn": "Shona", "sd": "Sindhi",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "es": "Spanish", "su": "Sundanese", "sw": "Swahili", "sv": "Swedish",
    "tl": "Tagalog", "tg": "Tajik", "ta": "Tamil", "tt": "Tatar",
    "te": "Telugu", "th": "Thai", "bo": "Tibetan", "tr": "Turkish",
    "tk": "Turkmen", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "vi": "Vietnamese", "cy": "Welsh", "yi": "Yiddish", "yo": "Yoruba",
}

# Legacy mode names — the original 3-language picker accepted lowercased
# English names. Keep the table around so existing config.json files don't
# break and so the tk window's old constants resolve.
_LEGACY_MODE_TO_ISO: Dict[str, str] = {
    "english": "en", "spanish": "es", "italian": "it",
    # Pass-through for any other label spelled out in lowercase.
    **{label.lower(): iso for iso, label in WHISPER_LANGUAGES.items()},
}

# Public re-exports retained for callers that still iterate the original
# 3-mode list (tk window). They now point at the legacy subset to keep the
# old UI working until it's migrated to the full 99-language picker.
LANGUAGE_MODES: Tuple[str, ...] = ("english", "spanish", "italian")

LANGUAGE_MODE_LABELS: Dict[str, str] = {
    "english": "English",
    "spanish": "Spanish",
    "italian": "Italian",
}

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def resolve_iso(language: Optional[str]) -> Optional[str]:
    """Normalize an ``AppConfig.language`` value to a Whisper ISO code.

    Accepts:
    - ISO codes already in ``WHISPER_LANGUAGES`` ("en", "es", "haw", ...)
    - Legacy lowercase mode names ("english", "spanish")
    - Title-case labels ("English", "Spanish")
    - ``None`` / empty → returns ``None`` (whisper auto-detect)
    """
    if not language:
        return None
    val = language.strip()
    if val in WHISPER_LANGUAGES:
        return val
    lowered = val.lower()
    if lowered in _LEGACY_MODE_TO_ISO:
        return _LEGACY_MODE_TO_ISO[lowered]
    return None


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
    # Caret injection — when True, the tray simulates Ctrl+V into the focused
    # window after a hotkey-driven transcription, so the text lands at the
    # caret instead of just on the clipboard. Hotkey-flow only; tk-window
    # records are unaffected.
    auto_paste_after_hotkey: bool = True
    # Push-to-talk threshold: hold the hotkey ≥ this many ms and release
    # ends the take. Anything shorter is treated as a tap (toggle).
    ptt_threshold_ms: int = 300
    # Translation server URL — a second whisper-server instance loaded with
    # a translate-capable model (e.g. ggml-medium.bin). When the translate
    # toggle is on, requests route here instead of the primary turbo server.
    # Defaults to the local-llm-hub's :8091 contract.
    translate_base_url: str = "http://127.0.0.1:8091"
    # Optional webapp section — when missing, the tray spawns the webapp
    # on `:8443` with default settings. Set webapp.enabled:false to opt out.
    webapp: Dict = field(default_factory=dict)

    @property
    def whisper_language(self) -> Optional[str]:
        """ISO language code for the configured dictation mode (None = auto)."""
        return resolve_iso(self.language)

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
        auto_paste_after_hotkey=bool(raw.get("auto_paste_after_hotkey", True)),
        ptt_threshold_ms=int(raw.get("ptt_threshold_ms", 300)),
        translate_base_url=str(raw.get("translate_base_url") or "http://127.0.0.1:8091"),
        webapp=raw.get("webapp") or {},
    )


def _validate(raw: Dict) -> None:
    if "language" in raw and resolve_iso(raw["language"]) is None:
        raise ValueError(
            f"language must be a Whisper ISO code or English name "
            f"(e.g. 'en', 'english'); got {raw['language']!r}"
        )
    if "log_level" in raw and raw["log_level"] not in VALID_LOG_LEVELS:
        raise ValueError(f"log_level must be one of {VALID_LOG_LEVELS}")
    if "max_record_seconds" in raw:
        v = raw["max_record_seconds"]
        if not isinstance(v, int) or v <= 0:
            raise ValueError("max_record_seconds must be a positive integer")
