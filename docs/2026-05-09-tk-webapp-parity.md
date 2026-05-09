# 2026-05-09 — tk window ↔ webapp parity (Reset, editable transcript, force built-in mic)

## What was done

Aligned the three desktop-relevant header tools that the webapp exposed
but the tk main window did not. Skipped History and Incognito — they
require routing the desktop record flow through the webapp's
`/api/sessions` archive, which is a much larger refactor for arguably
no daily benefit (History is one click away in the browser).

### Added to `app/gui/app.py`

1. **🧽 Reset button** on the *Last transcription* header row
   (between *➕ Append* and *📋 Copy*). Clears both Text widgets,
   `_last_transcription`/`tray.last_transcription`, `_last_polished`,
   and `_displayed_last_transcription`. Mirrors the webapp's Reset.
2. **Editable transcript** — `last_text` Text widget no longer toggles
   `state=DISABLED`. A `<KeyRelease>` binding pushes edits back to the
   tray (or local) `last_transcription` slot so Polish picks them up.
   `_run_polish` and `_copy_last` also call the sync handler at entry
   so mouse-pasted edits (which don't fire `<KeyRelease>`) are picked
   up too.
3. **Force built-in mic toggle** — `Checkbutton` under the *Mic* combo.
   Default seeded from `webapp_config.force_builtin_mic_default`.
   `_apply_mic_selection()` (renamed from `_on_mic_change`) now
   combines mic combo + checkbox into a single `preferred_mics` list:
   - explicit combo pick → `[label]`
   - *System default* + checkbox on → `["realtek", "built-in",
     "internal"]` (matches existing positive-substring behaviour in
     `recorder.py:select_device`; no recorder change needed)
   - *System default* + checkbox off → `None`

### Files modified

- `app/gui/app.py` — three feature changes above.
- `README.md` — extended *Incognito mode* clarification (tk/tray are
  effectively always-incognito because they don't archive) and added a
  new *Tk window controls* subsection documenting the new Append /
  Reset / editable / force-built-in mic surfaces.

### Files NOT modified

- `app/gui/tray.py` — tray menu deliberately left as-is. Its job is
  hotkey + lifecycle + URL-copy; pickers would crowd it without
  speeding anything up.
- `src/recorder.py` — `select_device()`'s positive-substring match is
  enough to bias toward built-in inputs; no negative-filter
  mechanism needed.
- Webapp surfaces — unchanged.

## Validation run

```powershell
& .\.venv\Scripts\python.exe -m py_compile app\gui\app.py
```

Manual smoke test (when launched from tray):

- Dictate via hotkey → transcript appears in tk window → edit a word
  → tap *✨ Polish* → polished output reflects the edit.
- Tap *🧽 Reset* → both panels clear; tray's `last_transcription` is
  also cleared; *➕ Append* still on continues to work normally on
  the next take.
- Toggle *Force built-in mic* with *Mic* combo at *System default* →
  `config.preferred_mics` flips between `None` and the
  built-in heuristic list.

## Out of scope (deliberate)

- Webapp's *History* panel (refresh / copy selected / clean / per-row
  redo + copy + delete) — desktop flow doesn't archive sessions.
- Webapp's *🕵️ Incognito* — meaningless without an archive.
- Webapp's *Auto-clean older than (days)* setting — webapp-archive
  specific.
- Webapp's read-only *prompt preview* in Settings — the tk window's
  existing **👁 Show prompt** popup already covers it.
