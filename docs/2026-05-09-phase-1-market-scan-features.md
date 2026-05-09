# Phase 1 — market-scan features

Closes the bulk of the gap to Wispr Flow / Superwhisper / Aqua Voice
identified in `docs/market-scan/2026-05-09-initial-scan.md`. Three
sub-phases shipped together; commit may be split with `git add -p` if
desired.

## Sub-phase 1a — caret injection + F10 tap/hold

The hotkey is now a single key with two interaction modes:

- **Tap F10** → start recording; tap again to stop.
- **Hold F10** ≥ `ptt_threshold_ms` (default 300 ms) → push-to-talk;
  release to stop.

Modifier-combo hotkeys (`<ctrl>+<alt>+<space>`) fall back to
toggle-only — holding a 3-key chord for PTT is awkward UX.

After transcription, when `auto_paste_after_hotkey` is on (default), the
tray simulates `Ctrl+V` into the focused window via `pynput`'s keyboard
controller. The text lands at the caret instead of just on the clipboard
— a Wispr-style "type into the focused app" UX, but locally and free.

Hotkey-flow only by design: tk-window records and webapp records are
unaffected (the webapp can't focus a desktop window anyway).

**Files modified:** `src/inject.py` (new), `src/app_config.py`,
`config/config.json`, `app/gui/tray.py`.

## Sub-phase 1b — custom vocabulary + auto-snippets

Two new opt-in features wired into `TranscriptionClient` so all four
call sites (tray, webapp, tk, CLI) inherit transparently:

- **Vocabulary** — `config/vocabulary.json` (gitignored) holds
  per-language buckets of proper nouns, brands, jargon. Joined into the
  whisper-server `prompt` field so the decoder biases toward those
  words. Schema: `{"all": [...], "en": [...], "es": [...]}`.
- **Snippets** — `config/snippets.json` (gitignored) holds short keys
  auto-expanded in the transcript before clipboard / caret paste.
  Case-insensitive, word-boundary matching. Schema:
  `{"ttyl": "talk to you later", ...}`.

Both files hot-reload on mtime change — no restart needed. Sample files
(`*.sample.json`) shipped with the schema for easy onboarding.

**Files modified:** `src/vocabulary.py` (new), `src/snippets.py` (new),
`config/vocabulary.sample.json` (new), `config/snippets.sample.json`
(new), `.gitignore`, `src/transcription_client.py`.

## Sub-phase 1c — all 99 languages + translate toggle

`WHISPER_LANGUAGES` constant in `app_config.py` mirrors whisper.cpp's
full 100-entry `g_lang` table. Both webapp and tk window expose:

- **Language picker** — alphabetical list of all supported languages
  with English labels. Stored as ISO codes; legacy lowercase mode names
  (`"english"`) still resolve via `resolve_iso()`.
- **🌐 Translate to English** toggle — ephemeral, off on every launch.
  When on, the request routes to `translate_base_url` (default
  `http://127.0.0.1:8091`) with `task=translate`. When off, request
  routes to the primary turbo server as usual.

The tray hotkey path is intentionally translate-free — F10 always
transcribes in the configured language. Translation is a deliberate
"sit at the webapp / tk window" workflow.

`TranscriptionClient` now stores both base URLs and selects per-request
based on the `translate` flag. Single shared `requests.Session`.

**Files modified:** `src/app_config.py`, `src/transcription_client.py`,
`src/__init__.py`, `config/config.json`, `app/webapp/server.py`,
`app/webapp/static/index.html`, `app/webapp/static/app.js`,
`app/gui/app.py`, `app/gui/tray.py` (client init only).

### Translation server contract

Per the sibling `claude-local-calls` hub:

| Field    | Value                                              |
|----------|----------------------------------------------------|
| Endpoint | `POST http://127.0.0.1:8091/v1/audio/transcriptions` |
| Form     | `task=translate` selects translation              |
| Model    | `ggml-medium.bin` (CPU, lazy-spawn proxy)         |
| Cold     | 3–8 s on first call after 5 min idle              |
| Idle     | proxy SIGTERMs the child after 5 min of no calls  |

If `:8091` isn't running, the toggle gracefully no-ops at the network
level — the user sees a clear `502` toast. The default-off toggle keeps
existing single-server setups unaffected.

## Verification

- `py_compile` clean across all touched files.
- `create_app()` boots the FastAPI app with all routes; client picks up
  `translate_base_url` from `config.json`.
- Vocabulary and snippet hot-reload verified end-to-end in a temp-file
  smoke test.
- Hotkey parser maps `<F10>` → `Key.f10`, modifier combos → `None`
  (correctly falling back to toggle-only).

Real-time GUI smoke testing happens in the live session — recording
through the F10 hotkey + caret paste + translate toggle on a non-English
clip via `:8091`.

## Live test: translate proxy interop

End-to-end testing surfaced a contract mismatch between this client and
the sibling local-llm-hub's `:8091` translate proxy. Two rounds of
discovery:

### Round 1 — proxy was dropping `task` entirely

First live test: 🌐 toggle on, Italian audio in → Italian text out. The
CPU on the home PC spiked (so `:8091` was being hit and the medium model
loaded), but the response was the original Italian, not English.

Curl probes confirmed the proxy was forwarding only the `file` blob and
ignoring all other multipart fields. Reported back to the sibling; they
shipped a fix in `whisper_translate_proxy.py` that parses the multipart
body and rewrites `task=translate` → `translate=true` for the upstream
whisper-server child.

### Round 2 — `language` masks `task` in the upstream

Re-test after the round-1 fix: still Italian out. Curl narrowed it down
further:

```
curl -F file=@italian.wav -F language=it -F task=translate \
  http://127.0.0.1:8091/v1/audio/transcriptions
→ Italian (BUG — task is being ignored when language is set)

curl -F file=@italian.wav -F task=translate \
  http://127.0.0.1:8091/v1/audio/transcriptions
→ English (correct)
```

The upstream whisper-server treats `language=` as a hard "transcribe in
this language" hint that overrides `task=translate`. OpenAI's contract
treats them as orthogonal (`language` = source, `task` = output mode),
so this is a sibling-side bug.

**Workaround** in `src/transcription_client.py`: when `translate=True`,
omit the `language` field. Whisper auto-detects source language reliably
and translation always outputs English regardless, so the hint is
redundant. The branch is flagged with a TODO so it can be removed once
the sibling proxy honours the OpenAI contract.

```python
if iso and not translate:
    data["language"] = iso
if translate:
    data["task"] = "translate"
```

End-to-end Italian → English now works on the live `:8091` instance.

## Out of scope (deferred)

- Voice command mode (`"flow:"` prefix routing previous take through
  polish) — Phase 2.
- App-aware polish style (foreground window detection) — Phase 2.
- Auto-stop on silence (VAD) — Phase 2.
- Live streaming preview — Phase 3, deliberately skipped per the scan.
- Usage analytics dashboard — Phase 3, low ROI.
