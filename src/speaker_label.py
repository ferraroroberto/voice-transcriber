"""Strip whisper's fabricated leading speaker-attribution labels.

whisper large-v3-turbo sometimes prepends a phantom speaker label to an
otherwise-correct transcript when the take opens on a quiet/breathy
lead-in — e.g. ``Claudio Couto, Ph.D.: <your real words>``. The name is
never spoken; it's a training-data hallucination glued to the front of
the real text. Removing it at the shared ``TranscriptionClient``
chokepoint means every surface (tray, tk GUI, webapp) inherits the fix.

Two complementary rules, both applied only to a ``Label:`` at the very
start of the transcript and never when stripping would empty the text:

1. **Honorific / attribution** — the leading label carries an academic
   or professional degree (``Ph.D.``, ``M.D.``, ``J.D.``, ``Ed.D.``,
   ``MBA``, …), an honorific prefix (``Dr.``, ``Prof.``/``Professor``),
   or is a generic diarization tag (``Speaker N``). These are virtually
   never dictated, so the strip is safe without any per-name config.

2. **Configurable blocklist** — names keyed in
   ``config/speaker_blocklist.json`` (gitignored) are stripped even
   without a title, for recurring bare hallucinations like
   ``Claudio Couto:``. The file is an object keyed by name (values are
   free-text notes; ``_``-prefixed keys are ignored), opt-in, and
   hot-reloads on mtime change, mirroring ``src/snippets.py`` /
   ``src/vocabulary.py``.

A bare ``Roberto:`` or a real sentence that merely contains a colon
(``Today: I woke up``) is left untouched.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_BLOCKLIST_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "speaker_blocklist.json"
)
_LOCK = threading.Lock()
_CACHE: Tuple[float, Optional["re.Pattern[str]"]] = (0.0, None)

# A leading ``label:`` whose label carries an academic/professional title
# or is a generic diarization tag. The name portion is length-bounded so a
# real sentence that happens to contain a colon is never swallowed. Matched
# case-sensitively for the name (whisper emits Title-Case labels), and the
# ``:`` must be followed by whitespace + real content (handled by the caller).
_TITLED_LABEL = re.compile(
    r"^\s*(?:"
    # name + trailing academic degree, e.g. "Claudio Couto, Ph.D.:"
    r"[A-ZÀ-Þ][^:\n]{0,58}?,\s*"
    r"(?:Ph\.?\s?D|M\.?\s?D|J\.?\s?D|Ed\.?\s?D|D\.?\s?Phil|M\.?B\.?A|Esq)\.?"
    r"|"
    # honorific prefix + name, e.g. "Dr. Smith:", "Professor Jones:"
    r"(?:Professor|Prof|Dr|Mx)\.?\s+[A-ZÀ-Þ][^:\n]{0,40}?"
    r"|"
    # generic diarization label, e.g. "Speaker 1:", "SPEAKER 02:"
    r"(?i:speaker)\s+\d{1,3}"
    r")\s*:\s+",
)


def _load_blocklist_pattern() -> Optional["re.Pattern[str]"]:
    """Compile a ``^name:`` matcher from the gitignored blocklist file.

    Returns ``None`` when the file is missing, empty, or invalid — the
    feature is opt-in and must never block transcription.
    """
    global _CACHE
    try:
        mtime = _BLOCKLIST_PATH.stat().st_mtime
    except FileNotFoundError:
        return None
    cached_mtime, cached_re = _CACHE
    if mtime == cached_mtime and cached_re is not None:
        return cached_re
    with _LOCK:
        try:
            raw = json.loads(_BLOCKLIST_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"⚠️  speaker_blocklist.json invalid: {exc}")
            return None
        if not isinstance(raw, dict):
            logger.warning("⚠️  speaker_blocklist.json must be an object keyed by name")
            return None
        # Keys are the names; values are free-text notes. Skip the `_comment`
        # documentation key (and any other `_`-prefixed key) like the sibling
        # snippets / vocabulary configs.
        names: List[str] = [
            str(k).strip()
            for k in raw
            if str(k).strip() and not str(k).startswith("_")
        ]
        if not names:
            return None
        # Longest-first so "Claudio Couto" wins over a stray "Claudio".
        names.sort(key=len, reverse=True)
        pattern = re.compile(
            r"^\s*(?:" + "|".join(re.escape(n) for n in names) + r")\s*:\s+",
            re.IGNORECASE,
        )
        _CACHE = (mtime, pattern)
        return pattern


def _strip_once(text: str, pattern: "re.Pattern[str]") -> str:
    """Remove a single leading label if doing so leaves real content behind."""
    stripped = pattern.sub("", text, count=1)
    if stripped != text and stripped.strip():
        return stripped.lstrip()
    return text


def strip_speaker_label(text: str) -> str:
    """Drop a fabricated leading speaker-attribution label from ``text``.

    Honorific/attribution labels are removed unconditionally; configured
    blocklist names are removed even without a title. A transcript that is
    *only* a label (nothing after the colon) is returned unchanged.
    """
    if not text:
        return text
    out = _strip_once(text, _TITLED_LABEL)
    blocklist = _load_blocklist_pattern()
    if blocklist is not None:
        out = _strip_once(out, blocklist)
    return out
