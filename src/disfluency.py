"""Strip spoken filler words from a transcript.

The ASR this app talks to is a *verbatim* recogniser — ``parakeet-tdt``
(local-llm-hub's ``transcribe`` role primary) writes down every ``uh`` and
``um`` the speaker actually said, and the whisper fallback keeps most of
them too. Measured over the archive at the time of writing that is ~23
filler words per 1000, of which ``uh`` and ``um`` are 96%.

Nothing downstream removed them: the LLM ``polish`` pass (``src/polish.py``)
that exists for exactly this is a manual button on the webapp and tk GUI,
never reached by the tray hotkey or the ``/api/transcribe`` path — the two
that feed the clipboard. This module is the deterministic, zero-latency
half of that job, applied at the shared ``TranscriptionClient`` chokepoint
so every surface (tray, tk GUI, webapp, CLI, API) inherits it, the same
way ``speaker_label.py`` and ``snippets.py`` already do. The LLM polish
stays available for what a regex can't do — false starts and repetitions.

**Built-in list** (on by default): ``uh``, ``um``, ``uhm``, ``erm``,
``hmm``, ``mmm``, each also matching its elongations (``uhh``, ``ummm``, …)
because the final letter is allowed to repeat.

**Deliberately excluded:** ``oh``, ``ah``, ``eh``, ``er`` and bare ``mm``.
Those carry meaning in real dictation — *"Oh, and by the way …"*, *"he
said, Oh thank you"* — so stripping them would rewrite sentences to fix a
handful of occurrences. Anyone who wants them adds them to the config.

**Config** (opt-in, gitignored, hot-reloaded like its sibling configs)::

    config/disfluencies.json
    {"enabled": true, "fillers": ["uh", "um", "oh"]}

Missing or invalid file → the built-in defaults. ``fillers`` *replaces* the
built-in list (``[]`` therefore strips nothing); ``enabled: false`` turns
the filter off entirely. See ``config/disfluencies.sample.json``.

Removal is punctuation- and case-aware, not a bare ``\\buh\\b`` → ``""``:
a run of fillers collapses once (``"that is uh uh she"``), a filler that
opened the sentence takes its comma with it and hands the capital to the
next word (``"Uh, instead"`` → ``"Instead"``), and a take that is *only*
fillers is returned unchanged rather than emptied.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .hot_reload_json import MtimeCachedJson

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "disfluencies.json"

# Unambiguous English fillers only — see the module docstring for what is
# deliberately left out and why.
DEFAULT_FILLERS: Tuple[str, ...] = ("uh", "um", "uhm", "erm", "hmm", "mmm")

# A word character, apostrophe or hyphen on either side means we're inside a
# word ("uhuru", "ah-ha") — never a standalone filler.
_BOUNDARY = r"[\w'’\-]"

# Sentence-final punctuation: a filler following one of these opened a new
# sentence, so it may have been capitalised and may be hiding its own comma.
_SENTENCE_END = ".!?…"

# Punctuation a sentence-opening filler is allowed to take with it.
_ORPHAN_PUNCT = ".,;:!?"

# Characters that may sit between a removed sentence-opening filler and the
# word owed its capital — whitespace and opening quotes/brackets/dashes.
_CAP_SKIP = " \t\n\r\"'“”‘’([{-–—…«»"

_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")
_SPACE_RUN = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True)
class _Config:
    """Resolved filter state: ``pattern is None`` means "strip nothing"."""

    pattern: Optional["re.Pattern[str]"]


def _term_pattern(term: str) -> str:
    """Compile one filler into a regex fragment allowing its elongations.

    ``uh`` → ``uh+`` so ``uhh`` / ``uhhh`` match too. Terms shorter than two
    characters are rejected by :func:`_compile_pattern` — a one-letter term
    would become ``a+`` and eat the article.
    """
    return re.escape(term[:-1]) + re.escape(term[-1]) + "+"


def _compile_pattern(terms: List[str]) -> Optional["re.Pattern[str]"]:
    """Build the filler-run matcher, or ``None`` when there is nothing to strip."""
    clean: List[str] = []
    seen = set()
    for raw in terms:
        term = str(raw).strip().lower()
        if len(term) < 2 or not term.isalpha():
            if term:
                logger.warning(f"⚠️  disfluencies.json: ignoring filler “{term}”")
            continue
        if term not in seen:
            seen.add(term)
            clean.append(term)
    if not clean:
        return None
    alt = "(?:" + "|".join(_term_pattern(t) for t in clean) + ")"
    # One filler, then any run of further fillers glued by spaces/commas
    # ("uh uh", "um, uh"), then an optional trailing comma or semicolon.
    return re.compile(
        rf"(?<!{_BOUNDARY}){alt}(?:[\s,]+{alt})*\s*[,;]?(?!{_BOUNDARY})",
        re.IGNORECASE,
    )


_DEFAULT_CONFIG = _Config(pattern=_compile_pattern(list(DEFAULT_FILLERS)))


def _parse(raw: object) -> Optional[_Config]:
    """Turn the decoded ``disfluencies.json`` into a :class:`_Config`.

    Returns ``None`` for anything malformed so the loader falls back to the
    built-in defaults — a broken config must never stop transcription, and
    must never silently disable a filter the user believes is on.
    """
    if not isinstance(raw, dict):
        logger.warning("⚠️  disfluencies.json must be a JSON object — using defaults")
        return None
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        logger.warning("⚠️  disfluencies.json: 'enabled' must be true/false — using defaults")
        return None
    if not enabled:
        return _Config(pattern=None)
    terms = raw.get("fillers")
    if terms is None:
        return _DEFAULT_CONFIG
    if not isinstance(terms, list):
        logger.warning("⚠️  disfluencies.json: 'fillers' must be a list — using defaults")
        return None
    return _Config(pattern=_compile_pattern(terms))


_loader: MtimeCachedJson[Optional[_Config]] = MtimeCachedJson(
    _CONFIG_PATH, _parse, None, label="disfluencies.json"
)


def _cap_first(text: str) -> Tuple[str, bool]:
    """Upper-case the first letter of the word ``text`` opens with.

    Returns the text plus whether the capital is *still* owed — ``True``
    only when ``text`` ran out before any word started, so the caller
    carries the obligation to the next chunk. A sentence that opens with a
    number (``"Uh, 3 of them left"``) discharges it with no change: the
    capital belongs to that first word, and giving it to the one after
    would capitalise mid-sentence.
    """
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1 :], False
        if ch not in _CAP_SKIP:
            return text, False
    return text, True


def _at_sentence_start(last_char: str) -> bool:
    """Would the next word open a sentence, given the last character emitted?"""
    return not last_char or last_char in _SENTENCE_END


def _strip(text: str, pattern: "re.Pattern[str]") -> Tuple[str, bool]:
    """Remove every filler run from ``text``; returns ``(out, changed)``."""
    out: List[str] = []
    idx = 0
    owed_capital = False
    changed = False
    # Last non-whitespace character emitted so far — tracked incrementally
    # rather than re-joining ``out`` per match, which would be quadratic.
    last_char = ""

    for match in pattern.finditer(text):
        if match.start() < idx:
            # Overlaps punctuation an earlier match already consumed.
            continue
        chunk = text[idx : match.start()]
        if owed_capital:
            chunk, owed_capital = _cap_first(chunk)
        out.append(chunk)
        trimmed = chunk.rstrip()
        if trimmed:
            last_char = trimmed[-1]
        changed = True

        end = match.end()
        if _at_sentence_start(last_char):
            # The filler opened the sentence: swallow the punctuation it was
            # hiding behind ("Uh. Instead …" must not leave a stray period)
            # and hand its capital to whatever word comes next.
            probe = end
            while probe < len(text) and text[probe] in " \t":
                probe += 1
            if probe < len(text) and text[probe] in _ORPHAN_PUNCT:
                end = probe + 1
            if match.group(0)[:1].isupper():
                owed_capital = True
        idx = end

    tail = text[idx:]
    if owed_capital:
        tail, _ = _cap_first(tail)
    out.append(tail)
    return "".join(out), changed


def strip_disfluencies(text: str) -> str:
    """Remove configured filler words from ``text``.

    Returns ``text`` byte-for-byte unchanged when the filter is disabled,
    when no filler is present, or when stripping would leave nothing behind
    (a take that was only fillers is still a take the user recorded).
    """
    if not text:
        return text
    config = _loader.load() or _DEFAULT_CONFIG
    if config.pattern is None:
        return text

    out, changed = _strip(text, config.pattern)
    if not changed:
        return text

    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    out = _SPACE_RUN.sub(" ", out).strip()
    return out if out else text
