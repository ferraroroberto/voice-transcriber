# 2026-05-09 — Append mode + silence gate + history multi-select

## What was done

### Append mode (webapp + tray + tk)
A new **➕ Append** toggle on every surface. With it on, each new take
is glued onto the existing transcript with a blank-line separator
instead of replacing it — built for the "I move between locations and
want one big transcript across multiple records" flow. Ephemeral
(off on every fresh launch / page load), no persistence to
`webapp_config.json`.

- **Webapp**: checkbox in the top-right header next to **🧽 Reset**.
  State held in JS only. `mergeForAppend(prev, next)` glues with
  `\n\n`; called in `onRecorderStopped` and `retranscribe`. Auto-copy
  reads from `els.transcript.value` (the visible textarea) so the
  full accumulated bundle lands on the clipboard, not just the
  latest take.
- **Tray + tk window** share one `tray.append_mode: bool` flag.
  Tray gets a checkable menu item `➕ Append mode` (uses pystray's
  `checked=` lambda). Tk window gets a `Checkbutton` on the *Last
  transcription* row. The window registers a callback via
  `tray.add_append_listener()` so menu↔checkbox stay in sync.
  Standalone tk (no tray) falls back to its own local flag. Both
  `_transcribe_worker` (tray hotkey path) and `_transcribe_and_show`
  (tk standalone) prepend before storing/copying.

### Silence gate before whisper
Whisper.cpp hallucinates plausible-sounding text on silent input
("Thanks for watching!", "[Music]", a single "you", etc.). New
`src/silence.py` exposes `rms_dbfs_from_samples()` (numpy int16 or
float) and `rms_dbfs_from_wav()` (stdlib `wave` + numpy). Both webapp
and tk/tray now compute peak RMS dBFS before invoking whisper and
skip transcription if it falls below `silence_dbfs_threshold`
(default `-50` dBFS, configurable in `config/webapp_config.json`).

- **Webapp** (`_transcribe_session_payload` in `app/webapp/server.py`):
  reads the transcoded WAV with `rms_dbfs_from_wav`. Silent take →
  writes empty `transcript.txt`, stores `silence_dbfs` in
  `meta.extra`, returns 200 with `{"silent": true, "dbfs": X}`.
  Frontend displays `🤫 Empty audio (X dBFS) — skipped` and
  preserves any accumulated transcript already in the box.
- **Tk/tray**: works on the int16 numpy array already in memory
  (no extra disk read).
- New config field `WebappConfig.silence_dbfs_threshold: float`,
  loaded with a fall-back to `-50.0` so existing config files keep
  working unchanged. Documented in `webapp_config.sample.json`.

Fail-open: if the WAV can't be read, the function returns 0 dB so
the gate passes through and whisper still runs. Silence detection
is best-effort, never a blocker.

### History multi-select Copy
The single-shot **📋 Copy last** button (which lasted one iteration)
was replaced with **📋 Copy selected**, driven by a checkbox on every
history item.

- Each `<li>` becomes a flex row with a left-side checkbox label and
  a right-side `.content` wrapper holding the existing when/preview/
  actions.
- `refreshHistory()` re-renders the list and then auto-checks the
  first (newest) item, so the common one-click "grab the latest"
  flow still works.
- `onCopySelection()` collects every checked checkbox, reverses the
  list (rendered newest-first → chronological oldest-first), fetches
  each take's full text from `GET /api/sessions/{id}/text`, and
  joins with `\n\n` so take boundaries stay visible after pasting.
- Empty selection or text-less takes show a toast instead of writing
  empty content to the clipboard.

### History/UX polish
- New `GET /api/sessions/{id}/text` endpoint — returns
  `{transcript, polished}` from disk. Used by the per-item Copy
  button and Copy selected. Replaces the previous behaviour where
  the per-item Copy was reading the 200-char `transcript_preview`
  and silently truncating long takes.
- History panel is now `<details open>` so the action row is always
  reachable without an extra tap.
- All three action buttons share the same neutral `ghost-btn` style
  (no more standalone red Clean-all). Clean briefly flashes red on
  successful delete, mirroring the green flash other Copy buttons
  use.
- Action row right-aligned via `justify-content: flex-end`,
  consistent with the per-item action buttons and the card-header
  actions on transcript/polish cards.
- Transcript textarea shrunk `rows="5"` → `rows="4"`, polish
  textarea `rows="5"` → `rows="3"`, freeing vertical space for the
  default-open history panel.
- Clean all → **Clean**, Copy selection → **Copy selected** so all
  three buttons fit one row on a phone.

### Auto-copy feedback
- Webapp `tryAutoCopy(text, btn)` now flashes the passed-in Copy
  button to `✓ Copied` for 1.4 s on a successful clipboard write —
  was silently auto-copying with no UI feedback before, which felt
  like nothing happened (and on iOS, where Safari often rejects
  clipboard writes outside a user gesture, it actually was nothing).
  When the write fails the button is left untouched so the user can
  tap it manually.
- Tk window now auto-copies the polished text after a successful
  polish (was clipboard-on-click only) and flashes the
  `📋 Copy polished` button to `✓ Copied`.

### Cache busting
The script reference in `index.html` carries `?v=N` so a hard
refresh isn't needed for users who had the page open during the
deploy. Bumped each iteration that touched `app.js`.

## Files modified

- `app/webapp/server.py` — `/api/sessions/{id}/text` endpoint,
  silence gate in `_transcribe_session_payload`.
- `app/webapp/static/index.html` — Append toggle, Copy selected,
  Clean (renamed), `<details open>`, cache-buster.
- `app/webapp/static/app.js` — `mergeForAppend`, `flashCopied`,
  `flashDanger`, `onCopySelection`, defensive `els.transcript.value`
  auto-copy, history checkbox rendering.
- `app/webapp/static/styles.css` — `.append-toggle`,
  `.history-actions { justify-content: flex-end }`, history li flex
  layout with left-side checkbox, ghost-btn flash variants.
- `app/gui/tray.py` — `append_mode` flag, listener wiring,
  `EVT_TOGGLE_APPEND`, `set_append_mode()`, silence gate in
  `_transcribe_worker`.
- `app/gui/app.py` — Append `Checkbutton`, `_on_append_toggle`,
  `_on_tray_append_changed`, `_is_append_mode`,
  `_flash_copied_polished`, polish auto-copy in `_polish_worker`,
  silence gate in `_transcribe_and_show`.
- `src/silence.py` — new module.
- `src/webapp_config.py` — `silence_dbfs_threshold` field.
- `config/webapp_config.sample.json` — example + comment.

## Validation

- `python -m py_compile` clean across all changed Python files.
- Module imports clean: `app.webapp.server`, `app.gui.app`,
  `app.gui.tray`.
- Smoke test on `src.silence`: zero-buffer reports `-120` dBFS and
  is gated; uniform white noise around int16 8000 ≈ `-12` dBFS and
  passes through.

## Out of scope (deliberate)

- No persistence of the Append toggle to `webapp_config.json`.
- No audio merging in append mode — each take is still archived as
  its own session; only the displayed/copied transcript is glued.
- No backend cross-session accumulator. Polish on the latest session
  receives the full merged text via the existing `transcript=` body
  field, so the latest archive entry ends up with the polished
  bundle.
