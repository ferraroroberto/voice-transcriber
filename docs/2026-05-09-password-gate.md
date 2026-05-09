# 2026-05-09 — Password gate as a companion to the bearer token

## What was done

Bootstrapping a fresh device with the bearer token meant pasting a
long `?token=…` URL once. On desktop that's ugly but workable; on
iOS PWAs it broke entirely because the PWA's `localStorage` is
partitioned from Safari's main jar in many configurations — the token
the user "stored" by visiting the URL in Safari was not visible from
inside the standalone PWA.

Added a small password gate as a UX wrapper around the existing token.
The user picks a memorable password (e.g. 6 digits); the page shows a
login overlay whenever an API call returns 401; entering the right
password triggers `/api/login`, which validates against
`webapp_config.auth_password` and hands the bearer token back to the
client. The client stashes the token in `localStorage` exactly as if
the tokenised URL had been opened — every subsequent API call uses
the existing `Authorization: Bearer …` flow unchanged.

The token gate is the actual security boundary; the password is a
typing-friendly second factor that the device exchanges for the
token. This keeps the existing API surface and middleware untouched.

## Files added

- `scripts/set_password.py` — set or clear `auth_password` in
  `config/webapp_config.json`. Emits a stdout reconfigure for
  Python 3.14 so emoji prints survive a cp1252 PowerShell.

## Files modified

- `src/webapp_config.py` — `WebappConfig.auth_password: str = ""`,
  threaded through load and save like the existing fields.
- `app/webapp/server.py`:
  - `/api/login` POST handler. Returns the bearer token on a
    correct password, 401 on a wrong one, 503 if either
    `auth_password` or `auth_token` is empty (the password is a
    no-op without a token to hand back).
  - `/api/login` added to the auth-exempt list so a tokenless
    device can reach it.
  - Dedicated `vt.auth` logger writing to `webapp/auth.log`
    (idempotent handler attach so reloads don't multiply the
    handler). Every login attempt — success or failure — is
    written there with the requesting client IP.
- `app/webapp/static/index.html` — login overlay markup
  (form + password field + error slot). Cache-bust to v=7.
- `app/webapp/static/styles.css` — `.login-overlay` (full-screen
  dark backdrop), `.login-card` (centred card), error slot.
- `app/webapp/static/app.js`:
  - `loadConfig()` now does a single direct fetch and detects 401
    explicitly, calling `promptForPassword()` and recursing on
    success.
  - `promptForPassword()` opens the overlay, focuses the input,
    posts to `/api/login`, distinguishes 503 / 401 / generic
    failure, stores the token on success, hides the overlay.
  - `els.{loginOverlay,loginForm,loginPassword,loginError}`
    references added.
- `config/webapp_config.sample.json` — documents the new field.
- `README.md` — new "Password gate (companion to the token)"
  subsection under the bearer-token section.

## Validation

- `python -m py_compile` clean across `set_password.py`,
  `webapp_config.py`, `server.py`.
- `from app.webapp import server` shows `/api/login` in the
  registered route list.
- Set test password via `scripts/set_password.py PW`,
  confirmed it persisted to `webapp_config.json`.

## Out of scope (deliberate)

- No rate limiting on `/api/login`. The login attempt cost is
  already a TLS handshake + a constant-time compare; a brute-force
  attacker hitting from the public internet would have to also
  bypass the URL-obscurity layer (random `whisper.<your-domain>`
  subdomain), and the per-attempt log row in `webapp/auth.log`
  makes attempts visible. If this becomes a concern, drop in
  `slowapi` middleware later.
- No session cookie path. The password swaps to the existing
  bearer token; existing middleware, existing tk-window loopback
  bypass, all unchanged.
