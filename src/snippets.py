"""Auto-replace short keys in transcripts with longer phrases.

Reads ``config/snippets.json`` (gitignored). Schema::

    {
      "ttyl": "talk to you later",
      "myemail": "roberto.ferraro@gmail.com"
    }

Matching is case-insensitive at word boundaries; the replacement value is
emitted verbatim. Applied just before the transcript hits clipboard / caret
paste, so every UI surface inherits the expansion through ``TranscriptionClient``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_SNIPPETS_PATH = Path(__file__).resolve().parent.parent / "config" / "snippets.json"
_LOCK = threading.Lock()
_CACHE: Tuple[float, Dict[str, str], Optional["re.Pattern[str]"]] = (0.0, {}, None)


def _load() -> Tuple[Dict[str, str], Optional["re.Pattern[str]"]]:
    global _CACHE
    try:
        mtime = _SNIPPETS_PATH.stat().st_mtime
    except FileNotFoundError:
        return {}, None
    cached_mtime, cached_map, cached_re = _CACHE
    if mtime == cached_mtime and cached_map:
        return cached_map, cached_re
    with _LOCK:
        try:
            raw = json.loads(_SNIPPETS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"⚠️  snippets.json invalid: {exc}")
            return {}, None
        if not isinstance(raw, dict) or not raw:
            return {}, None
        mapping = {str(k): str(v) for k, v in raw.items() if str(k).strip()}
        if not mapping:
            return {}, None
        # Sort longest-first so multi-word keys win over their prefixes.
        keys = sorted(mapping.keys(), key=len, reverse=True)
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b",
            re.IGNORECASE,
        )
        ci_mapping = {k.lower(): v for k, v in mapping.items()}
        _CACHE = (mtime, ci_mapping, pattern)
        return ci_mapping, pattern


def apply_snippets(text: str) -> str:
    """Expand any configured snippet keys in ``text``.

    Returns ``text`` unchanged when ``config/snippets.json`` is missing or
    empty — the file is opt-in.
    """
    if not text:
        return text
    mapping, pattern = _load()
    if not mapping or pattern is None:
        return text

    def _sub(match: "re.Match[str]") -> str:
        return mapping.get(match.group(0).lower(), match.group(0))

    return pattern.sub(_sub, text)
