"""Per-language custom vocabulary, joined into whisper.cpp's ``prompt`` field.

Whisper biases its decoder toward words listed in the prompt — useful for
proper nouns, brand names, and jargon it would otherwise mishear. The file
lives at ``config/vocabulary.json`` (gitignored). Buckets are keyed by ISO
language code, plus an optional ``"all"`` bucket merged into every language.

Schema::

    {
      "all": ["Roberto", "Ferraro"],
      "en":  ["Anthropic", "Claude", "whisper.cpp"],
      "es":  ["Anthropic"]
    }

Loaded once and cached against file mtime so edits hot-reload without a
restart.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .hot_reload_json import MtimeCachedJson

logger = logging.getLogger(__name__)

_VOCAB_PATH = Path(__file__).resolve().parent.parent / "config" / "vocabulary.json"


def _parse(raw: object) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        logger.warning("⚠️  vocabulary.json must be an object keyed by ISO code")
        return {}
    cleaned: Dict[str, List[str]] = {}
    for key, val in raw.items():
        if not isinstance(val, list):
            continue
        cleaned[str(key).lower()] = [str(v).strip() for v in val if str(v).strip()]
    return cleaned


_loader: MtimeCachedJson[Dict[str, List[str]]] = MtimeCachedJson(
    _VOCAB_PATH, _parse, {}, label="vocabulary.json"
)


def prompt_for_language(iso_code: Optional[str]) -> Optional[str]:
    """Return the comma-joined vocabulary string for ``iso_code``.

    The ``"all"`` bucket (if present) is merged in for every language.
    Returns ``None`` when nothing applies — the caller should omit the
    ``prompt`` form-field entirely in that case.
    """
    vocab = _loader.load()
    if not vocab:
        return None
    items: List[str] = list(vocab.get("all", []))
    if iso_code:
        items.extend(vocab.get(iso_code.lower(), []))
    seen: set = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    if not out:
        return None
    return ", ".join(out)
