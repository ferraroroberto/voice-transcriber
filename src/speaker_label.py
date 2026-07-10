"""Strip whisper's fabricated leading speaker-attribution labels.

whisper large-v3-turbo sometimes prepends a phantom speaker label to an
otherwise-correct transcript when the take opens on a quiet/breathy
lead-in — e.g. ``Claudio Couto, Ph.D.: <your real words>``. The name is
never spoken; it's a training-data hallucination glued to the front of
the real text. Removing it at the shared ``TranscriptionClient``
chokepoint means every surface (tray, tk GUI, webapp) inherits the fix.

Three complementary rules, all applied only to a label at the very start
of the transcript and never when stripping would empty the text:

1. **Honorific / attribution** — the leading label carries an academic
   or professional degree (``Ph.D.``, ``M.D.``, ``J.D.``, ``Ed.D.``,
   ``MBA``, …), an honorific prefix (``Dr.``, ``Prof.``/``Professor``),
   or is a generic diarization tag (``Speaker N``). These are virtually
   never dictated, so the strip is safe without any per-name config.

2. **Assistant-name family (committed, built-in)** — whisper, primed by
   local-llm-hub's ``--carry-initial-prompt`` boost term ``Claude Code``,
   re-emits a *misheard* variant of that name at the head of a take on a
   quiet lead-in: ``Cloud Code``, ``Claudio Couto``, ``Claudius C.``,
   ``Claude Coulson``, … — always a Title-Case proper name (root
   ``Cl[ao]ud…`` ± a surname/initial) glued to the real words either by an
   invented separator run **or** by whitespace alone. Because the family
   is recurring and self-describing, it is matched by a *committed* regex
   here rather than a per-variant hand-edit of the gitignored blocklist —
   so the fix travels with the repo and works on a fresh checkout. The
   correctly-spelled tool name ``Claude`` / ``Claude Code`` is carved out
   and left intact, so an intentional sentence opening with it survives.

3. **Configurable blocklist** — names keyed in
   ``config/speaker_blocklist.json`` (gitignored) are stripped even
   without a title, for any *other* recurring bare hallucination outside
   the built-in family. whisper glues the phantom name to the real words
   with whatever separator it invents, so the blocklist matches the name
   followed by a run of separator punctuation — colon **or** comma, dash,
   or bare period (``Claudius, ``, ``Claudio Pagliauvao- ``,
   ``Claudius C. ``) — not colon alone. The file is an object keyed by
   name (values are free-text notes; ``_``-prefixed keys are ignored),
   opt-in, and hot-reloads on mtime change via the shared
   ``src/hot_reload_json.py`` loader (also used by ``src/snippets.py`` and
   ``src/vocabulary.py``).

A bare ``Roberto:`` or a real sentence that merely opens with a comma'd
word (``Today: I woke up``, ``Ok, let me think``) is left untouched —
only titled labels, the assistant-name family, and opt-in blocklist names
are ever stripped, and never when doing so would empty the take.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from .hot_reload_json import MtimeCachedJson

logger = logging.getLogger(__name__)

_BLOCKLIST_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "speaker_blocklist.json"
)

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

# Built-in committed heuristic for the recurring assistant-name family
# (issue #79). The root is the misheard "Claude" — ``Cl[ao]ud…`` (``Cloud``,
# ``Claude``, ``Claudio``, ``Claudius``, …) — Title-Cased by whisper because
# it emits the phantom as a proper-name label. It may carry up to two more
# Title-Case name words (a surname, ``Code``, or an initial like ``C``); a
# trailing period is *not* folded into a name word so the period that glues a
# ``Claudius C.`` phantom to the real words reads as the separator, not part of
# the name. The repetition is non-greedy so the fewest name words are taken,
# leaving a Title-Case real first word (``Claudius C. Okay …`` → ``Okay …``)
# intact. Two leading forms: the name + an invented separator run, or — for a
# multi-word name only — the name + whitespace alone (a multi-word, all-Title-
# Case opener is the phantom's signature; normal sentence-case dictation that
# merely starts with a ``Cl[ao]ud`` word has a lowercase second word and is
# never matched here).
_FAMILY_ROOT = r"Cl[ao]ud\w*"
_NAME_WORD = r"[A-ZÀ-Þ][\w'’\-]*"
_ASSISTANT_FAMILY = re.compile(
    # (a) family name (bare root or +1–2 words) + invented separator run + ws
    r"^\s*(?P<sep_name>" + _FAMILY_ROOT + r"(?:\s+" + _NAME_WORD + r"){0,2}?)"
    r"\s*[.,:;\-–—]+\s+"
    r"|"
    # (b) multi-word family name (root +1–2 words) glued by whitespace alone
    r"^\s*(?P<ws_name>" + _FAMILY_ROOT + r"(?:\s+" + _NAME_WORD + r"){1,2}?)"
    r"\s+(?=\S)",
)
# The genuine tool name — left intact even though it shares the family root, so
# a take that legitimately opens "Claude Code, here is …" or "Claude, …" is not
# mangled. Only the *misheard* spellings (Cloud…, Claudio/Claudius…, Claude + a
# wrong surname) are stripped.
_REAL_TOOL_NAME = re.compile(r"Claude(?:\s+Code)?", re.IGNORECASE)


def _strip_assistant_family(text: str) -> str:
    """Remove a leading misheard-assistant-name phantom (built-in family).

    Leaves the correctly-spelled tool name (``Claude`` / ``Claude Code``)
    untouched, and never strips when doing so would empty the take.
    """
    match = _ASSISTANT_FAMILY.match(text)
    if match is None:
        return text
    name = (match.group("sep_name") or match.group("ws_name") or "").strip()
    if _REAL_TOOL_NAME.fullmatch(name):
        return text
    stripped = text[match.end() :]
    if stripped.strip():
        return stripped.lstrip()
    return text


def _parse_blocklist(raw: object) -> Optional["re.Pattern[str]"]:
    """Compile a ``^name<sep>`` matcher from the decoded blocklist JSON.

    Returns ``None`` when the content is missing, empty, or invalid — the
    feature is opt-in and must never block transcription.
    """
    if not isinstance(raw, dict):
        logger.warning("⚠️  speaker_blocklist.json must be an object keyed by name")
        return None
    # Keys are the names; values are free-text notes. Skip the `_comment`
    # documentation key (and any other `_`-prefixed key) like the sibling
    # snippets / vocabulary configs.
    names: List[str] = [
        str(k).strip() for k in raw if str(k).strip() and not str(k).startswith("_")
    ]
    if not names:
        return None
    # Longest-first so "Claudius S" wins over a bare "Claudius" (and
    # "Claudio Couto" over a stray "Claudio") — the alternation is tried
    # left-to-right, so the more specific name must come first.
    names.sort(key=len, reverse=True)
    # Name + a run of separator punctuation (colon, comma, dash, period)
    # + whitespace. whisper varies the separator it hallucinates, so the
    # match can't be colon-only; requiring trailing whitespace keeps a
    # real word glued to punctuation (e.g. an abbreviation) from matching.
    return re.compile(
        r"^\s*(?:" + "|".join(re.escape(n) for n in names) + r")\s*[.,:;\-–—]+\s+",
        re.IGNORECASE,
    )


_blocklist_loader: MtimeCachedJson[Optional["re.Pattern[str]"]] = MtimeCachedJson(
    _BLOCKLIST_PATH, _parse_blocklist, None, label="speaker_blocklist.json"
)


def _load_blocklist_pattern() -> Optional["re.Pattern[str]"]:
    return _blocklist_loader.load()


def _strip_once(text: str, pattern: "re.Pattern[str]") -> str:
    """Remove a single leading label if doing so leaves real content behind."""
    stripped = pattern.sub("", text, count=1)
    if stripped != text and stripped.strip():
        return stripped.lstrip()
    return text


def strip_speaker_label(text: str) -> str:
    """Drop a fabricated leading speaker-attribution label from ``text``.

    Honorific/attribution labels and the built-in assistant-name family are
    removed unconditionally; configured blocklist names are removed even
    without a title. A transcript that is *only* a label (nothing after the
    label) is returned unchanged.
    """
    if not text:
        return text
    out = _strip_once(text, _TITLED_LABEL)
    out = _strip_assistant_family(out)
    blocklist = _load_blocklist_pattern()
    if blocklist is not None:
        out = _strip_once(out, blocklist)
    return out
