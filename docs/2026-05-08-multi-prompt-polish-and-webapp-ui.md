# 2026-05-08 — Multi-prompt polish + webapp UI restructure

## What was done

### Webapp UI restructure
Mobile-first cleanup driven by a long history list overflowing the
viewport.

- **Settings** moved out of a slide-in `<aside>` and into a collapsible
  `<details class="settings-card">`, placed just above History (which
  was already a `<details>`). Both sections are now twin cards — the
  page is a simple linear stack with two collapsible blocks at the
  bottom. The top-right `⚙️` icon button is gone.
- **🧽 Reset** moved to the top-right of the header (replacing the gear
  icon). It used to live under the record button and ate vertical space.
- **Record button** shrunk from `min(48vw, 187px)` to `min(32vw, 125px)`
  (~2/3 size); label/timer fonts scaled to fit.
- **Transcript / Polished textareas** bumped from `rows=2` /
  `min-height: 64px` to `rows=5` / `min-height: 120px`. The previews
  were too small to actually read or edit.
- **Polish card collapsed** to a single header row:
  `✨ Polish` · `Go` button · `📋 Copy`. The model dropdown + ⭐ button
  moved into Settings, so the polish card is now title + actions +
  textarea. Nothing else.
- **Transcript card** got an icon to match (`🎙️ Transcript`).
- **`Go` replaces `✨ Polish transcript`** as the action button label —
  the card title already says ✨ Polish.

### Multi-prompt polish library
The system prompt was hard-coded in `src/polish.py`. It is now one entry
in a JSON library that the webapp + tk window read at boot. Adding new
polish styles ("grammar-only", "correctness", "raw-idea → prompt") in
the future is a JSON edit, not a code change.

- New file `config/polish_prompts.json` — committed, ships with one
  entry (`filler-words`).
- New module `src/polish_prompts.py` — `PolishPrompt` dataclass,
  `load_polish_prompts()`, `get_prompt(id)`. Falls back to a hard-coded
  built-in entry if the JSON file is missing or invalid, so polish
  never breaks because someone deleted the file.
- `src/polish.py` — `PolishClient.polish()` now accepts an optional
  `system=` override. Default behaviour is unchanged for any caller that
  doesn't pass it (legacy code path).
- `src/webapp_config.py` — added `polish_prompt_default: str =
  "filler-words"`. Persisted alongside the rest of the config.
- `src/archive.py` — `SessionMeta.polish_prompt_id` field.
  `write_polished()` and `mark_polish_failed()` now record which prompt
  the take was polished with, so History can render it.
- `app/webapp/server.py` — `/api/config` exposes `polish_prompts: [...]`
  and `polish_prompt_default`; `/api/sessions/{id}/polish` and
  `/api/polish-text` accept an optional `prompt_id`. Server resolves it
  to a system prompt and passes through to `PolishClient`.

### Webapp settings: prompt preview
Settings now contain a read-only textarea showing the system prompt
that will be sent for the currently selected polish style. Updates on
dropdown change. Read-only — editing comes later if needed.

### Tk parity
The tk main window mirrors the same feature set (per the project's
parity rule):

- A second dropdown next to the model picker selects the polish style.
- `⭐ Save defaults` (was `⭐ Default`) persists *both* the model and
  the style as the new defaults.
- New `👁 Show prompt` button opens a read-only popup with the system
  prompt text — equivalent to the webapp's settings preview.
- The polish call now passes the selected style's `system` text through
  to `PolishClient.polish()`.

## Files modified
- `app/webapp/static/index.html`, `app/webapp/static/styles.css`,
  `app/webapp/static/app.js`
- `app/webapp/server.py`
- `app/gui/app.py`
- `src/polish.py`, `src/webapp_config.py`, `src/archive.py`
- `config/webapp_config.sample.json`

## Files added
- `src/polish_prompts.py`
- `config/polish_prompts.json`
- `docs/2026-05-08-multi-prompt-polish-and-webapp-ui.md` (this file)

## Validation run

```text
./.venv/Scripts/python.exe -m py_compile src/polish_prompts.py \
    src/polish.py src/webapp_config.py src/archive.py \
    app/webapp/server.py app/gui/app.py
→ OK

# In-process import smoke test
prompts loaded: ['filler-words']
prompt resolved: filler-words - Filler-word cleanup
webapp_config polish_prompt_default: filler-words
server boot OK, routes: 21

# FastAPI TestClient dry run with PolishClient.polish patched
GET  /api/config                 → polish_prompts in payload, default 'filler-words'
POST /api/polish-text {prompt_id} → returned prompt_id 'filler-words',
                                    PolishClient.polish() got the right
                                    system text (838 chars, contains
                                    'filler') passed through
POST /api/polish-text (no prompt) → falls back to config default 'filler-words'
POST /api/config {polish_prompt_default} → echoed back

# Live uvicorn boot on 127.0.0.1:8765
GET /healthz       → 200 {'ok': True, ...}
GET /api/config    → polish_prompts present, default 'filler-words'
GET /static/index.html → 200, contains <select id="polishStyle">
```

## Restart instructions
- **Static-only changes** (HTML / CSS / JS): hard-reload the page.
- **Backend changes** (this changelog includes Python edits): restart
  the tray (right-click tray icon → Quit, then `tray.bat`) so uvicorn
  picks up the new endpoints. The tk main window also needs a restart
  for the new dropdown to appear.

## Follow-up: paginated history

Same day, second iteration. Heavy daily use means the 30-day archive
balloons and dragging the entire list into the page on every refresh
gets wasteful.

- `archive.list_sessions()` grew an `offset` parameter (newest-first);
  new `count_sessions()` returns the total.
- `GET /api/sessions` now defaults to `limit=10` and accepts `offset`.
  Response includes `total`, `offset`, `limit` so the client knows
  whether more pages exist.
- The webapp loads 10 by default; a `📥 Load more` button under the
  list fetches the next 10 and appends them. Hides itself when the
  full archive is shown. The summary shows `📜 History (10/23)` while
  more is available, and just `(23)` when everything is loaded.

The tk window's "last transcription" panel only ever showed one entry,
so no parity work was needed there.

## Adding new polish styles later

Append a new entry to `config/polish_prompts.json`:

```json
{
  "id": "grammar-only",
  "label": "Grammar fixes only",
  "description": "Fix spelling, grammar, punctuation. No content changes.",
  "system": "You are a copy editor. Fix only spelling, grammar, and punctuation..."
}
```

Restart the webapp + tk window. The new style appears in both
dropdowns. The webapp's settings preview shows the prompt text. No
Python changes required.
