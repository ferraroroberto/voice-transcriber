# 2026-05-09 — Per-row Delete, Incognito mode, Whisper rebrand, header polish

A small batch of webapp UX changes landed in this session. Lumped
together here so the dated changelog stays continuous.

## What was done

### Per-row delete in History
Each history row had `📋 Copy` and `🔁 Re-transcribe`; now also
`🗑️ Delete`. Clicking it pops a confirm dialog and, on accept,
calls `DELETE /api/sessions/{session_id}` — a new server endpoint —
which removes that one folder via `archive.delete_session()` and
prunes empty `YYYY/MM/DD` parents. The whole-history `🗑️ Clean`
button is unchanged.

### Incognito mode
A `🕵️` icon toggle in the header. When the outline turns blue, the
**next** session created via `POST /api/sessions` is flagged
`incognito=true`. The flag persists in `meta.json` and is read by:

- `archive.list_sessions()` — incognito sessions are filtered out
  before pagination, so the History panel never shows them.
- `archive.count_sessions()` — the same filter, so the `📜 History
  (N)` badge counts only visible sessions.

Client-side the flow is otherwise unchanged: the session is created,
chunks streamed, transcribed, optionally polished, copied — exactly
as a normal session. The difference is purely visibility (filtered
out of the list endpoint) plus an opportunistic disk cleanup: the
client tracks the last incognito session id in `state.incognitoSessionId`
and calls `DELETE /api/sessions/{id}` when the user hits **🧽 Reset**
or starts the next recording. The 30-day retention catches anything
the cleanup missed.

Webapp only — the tray + tk surfaces don't expose incognito yet.

### Whisper rebrand
- `<title>` → `🎙️ Whisper`
- H1 → `🎙️ Whisper`
- `apple-mobile-web-app-title` → `Whisper`
- `manifest.webmanifest` → `name: "Whisper"`, `short_name: "Whisper"`

iOS PWAs cache the manifest, so an existing home-screen icon keeps
the old name until removed and re-added — documented in the
"Heads-up on the iPhone" note at commit time.

### Header layout
Right-aligned, in order: `➕ Append | 🧽 | 🕵️`.

- The Reset button is now icon-only via a generic `.header-icon-btn`
  class (kept the `title` and `aria-label` so hover tooltip and
  screen readers still read "Reset").
- Same square outline as the incognito button so the two icons feel
  related visually.
- The old `.header-reset-btn` class was removed.

### Small fixes shipped along the way
- Login overlay was not disappearing after a successful login —
  `.login-overlay { display: flex }` was winning over the UA
  stylesheet's `[hidden] { display: none }` (UA rule isn't
  `!important`). Added `.login-overlay[hidden] { display: none }`
  with the same specificity advantage.
- Login overlay centering tightened on Firefox: explicit
  `top/left/right/bottom: 0` + `width: 100vw; height: 100vh` +
  `margin: 0; box-sizing: border-box`. Card got a stronger drop
  shadow and a tinted 1px outer glow so it pops off the dark
  backdrop.
- Per-row label `🔁 Re-transcribe` → `🔁 Redo` so the action row
  fits one line on a phone.

## Files modified

- `src/archive.py` — `SessionMeta.incognito`, threaded through
  `new_session()`, filtered in `list_sessions()` /
  `count_sessions()`, hydrated from `meta.json`,
  new `delete_session(id)` method.
- `app/webapp/server.py`:
  - `POST /api/sessions` accepts `incognito` flag.
  - `DELETE /api/sessions/{session_id}` endpoint.
- `app/webapp/static/index.html`:
  - Whisper rebrand on `<title>`, H1, `apple-mobile-web-app-title`.
  - Header reorder: Append | Reset | Incognito.
  - Reset becomes icon-only `🧽` button.
  - Incognito `🕵️` toggle.
- `app/webapp/static/manifest.webmanifest` —
  `Whisper` name + short_name.
- `app/webapp/static/styles.css`:
  - `.login-overlay[hidden] { display: none }` plus stronger
    centering rules and card shadow.
  - `.header-icon-btn` for the icon-only Reset.
  - `.incognito-btn` for the 🕵️ toggle (hidden checkbox + `:has()`
    selector for the active state).
- `app/webapp/static/app.js`:
  - `els.incognitoToggle` reference.
  - `state.incognitoSessionId` + `cleanupIncognitoSession()`.
  - Per-row `🗑️ Delete` button in `renderHistoryItem()`.
  - Re-transcribe button text → Redo.
- `README.md` — Append/Incognito/Reset wording, per-row buttons
  list updated to include Delete and rename Re-transcribe → Redo.

## Validation

- `python -m py_compile` clean across `archive.py`, `server.py`.
- `from app.webapp import server` shows `/api/sessions/{session_id}`
  registered with DELETE.
- Webapp opened on iPhone PWA + desktop Chrome + Firefox — login
  overlay centres and dismisses, header icons line up
  right-aligned, incognito toggle's checked state visible.

## Out of scope

- Tray + tk parity for incognito mode. The single-user webapp flow
  is the only one that genuinely needs "no trace" today; the
  desktop GUIs always run on the home PC and the user can already
  delete locally via History → 🗑️ Delete.
- Bulk delete via checkboxes. Copy-selected uses checkboxes for
  joining; mirror "delete-selected" later if it becomes a need.
