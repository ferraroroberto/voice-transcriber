# Modularize the webapp — split server.py + app.js into focused modules

**Issue:** #15 · **Date:** 2026-05-21

## What was done

Pure reorganization of the webapp, no behavior change — bringing
voice-transcriber in line with the sister app-launcher project (its
issue #26). The two monoliths were split into focused modules so adding
a feature no longer means re-reading hundreds of unrelated lines.

### Python — `server.py` (1070 LOC) → routers

`server.py` now keeps only the wiring: `create_app()`, the lifespan
hook, the `CachingStaticFiles` mount, middleware registration, and the
module-level `app`. Everything else moved:

- `app/webapp/middleware.py` — `BearerTokenMiddleware` + auth-exempt lists.
- `app/webapp/routers/_helpers.py` — `PROJECT_ROOT`, `STATIC_DIR`, `maybe_json`.
- `app/webapp/routers/misc.py` — `/`, `/healthz`, `/api/version`, `/install-ca`.
- `app/webapp/routers/config.py` — `GET/POST /api/config`, `GET /api/status`.
- `app/webapp/routers/auth.py` — `POST /api/login` + the `vt.auth` log handler.
- `app/webapp/routers/sessions.py` — the `/api/sessions*` family,
  `/api/polish-text`, `/api/save-text`, and the transcribe / polish /
  partial-worker helpers.

Routes reach their dependencies through `request.app.state` (already the
existing pattern); `BuildInfo` is now exposed as `app.state.build_info`.

### JS — `app.js` (1483 LOC IIFE) → ES modules

The single IIFE became a `<script type="module">` graph. The entry file
keeps the name `app.js`; feature logic split into:

- `state.js` — shared `els` / `state`, `TOKEN_KEY`, token helpers.
- `api.js` — `authFetch`, retry, login overlay, error-message extraction.
- `ui.js` — clipboard, toast, button flashes, formatting helpers.
- `config.js` — config load / render / persist, status poll.
- `recorder.js` — record lifecycle, chunk upload, VU meter, VAD,
  rolling-transcription SSE, background-finalise / resume.
- `history.js` — take list, render, redo, delete, copy-selection.
- `polish.js` — polish, save-text, reset.
- `app.js` — boot sequence + DOM event binding.

### Static versioning

`src/static_versioning.py`'s `BuildInfo` now hashes every `.js`/`.css`
file in `static/`, and `CachingStaticFiles` rewrites the
`import './x.js'` URLs in each module with a content hash at serve time
(`rewrite_js_imports`). `index.html` keeps its `?v=__APP_JS__` /
`?v=__STYLES_CSS__` placeholders for the two assets it references
directly. A stale module can no longer be served to an iPhone — the
hashed URL is the cache key.

## Files modified

- `app/webapp/server.py` (slimmed to ~190 LOC)
- `src/static_versioning.py`
- `app/webapp/static/app.js`, `index.html`
- New: `app/webapp/middleware.py`, `app/webapp/routers/{__init__,_helpers,misc,auth,config,sessions}.py`
- New: `app/webapp/static/{state,api,ui,config,recorder,history,polish}.js`
- `tests/conftest.py`, `tests/test_webapp_api_polish.py`,
  `tests/test_static_app_js.py` — import-path updates only.

## Validation

- `python -m compileall app src tests` — clean.
- `python -m pytest -q --ignore=tests/e2e` — 270 passed, 1 skipped.
- Throwaway `uvicorn` instance on a free port:
  - `/` stamps `app.js` / `styles.css`; entry doc is `no-cache`.
  - `/static/app.js` served with rewritten `import './x.js?v=…'` URLs,
    `immutable` cache, `text/javascript`.
  - `/api/version` `asset_hash` matches the `app.js` stamp in the HTML.
  - Page loaded in headless **Chromium and WebKit** — no JS errors,
    polish dropdowns populated, settings panel toggles.
