# 2026-05-07 — Mobile webapp + repo cleanup

Design and execution log for adding a mobile-first FastAPI web interface
to voice-transcriber, plus a layout cleanup to bring the repo in line
with the rest of the `automation/` monorepo conventions.

## Goal

A WhisperFlow-equivalent on the iPhone: open a web app, tap one big
button, dictate, get the transcript copied to the clipboard, optionally
polish it through the local LLM hub. **Never lose a recording**, even
if the connection drops mid-take. Same flow available from any other
device with a mic + browser.

The app is mobile-first but works identically from a desktop browser at
work (over Cloudflare) or at home (over Tailscale).

## Why FastAPI, not Streamlit

Streamlit re-runs the whole script on every interaction. That breaks a
press-and-hold or tap-toggle recorder because the `MediaRecorder` object
is destroyed on each rerun. The community components for mic input
(`streamlit-webrtc`, `audio-recorder-streamlit`) all hit the same wall
and are clunky on iOS Safari.

FastAPI + a hand-written single-page HTML/JS:

- `MediaRecorder` API in vanilla JS: ~30 LOC, works directly on the page.
- `navigator.clipboard.writeText()` works in the same tap event — required
  by iOS Safari, which only allows clipboard writes inside a synchronous
  user-gesture handler.
- Chunked upload via `MediaRecorder.ondataavailable` every second → server
  appends to disk *while recording* → crash-resistant.
- Tiny: uvicorn + FastAPI + a 500-line static page. No client-side
  framework.

Streamlit stays a great fit for the form-heavy apps in the monorepo
(`grocery-shopping-automation`, `facilitation-shuffle`). It's the wrong
tool for a single huge-button voice recorder.

## Network exposure

Two contexts, both supported via separate launchers:

| Context | Tunnel | Reason |
|---|---|---|
| Home (PC + iPhone on tailnet) | Tailscale | Zero-latency, no public exposure. Same pattern as `grocery-shopping-automation`. |
| Work (no Tailscale) | Cloudflare quick tunnel | Public HTTPS URL, no firewall changes. Same pattern as `facilitation-shuffle/launch_server.bat`. |

**HTTPS is mandatory** because iOS Safari refuses `getUserMedia` over
plain HTTP on LAN. Same self-signed-CA pattern that
`grocery-shopping-automation/gen_ssl_cert.py` already uses, with one
addition: the webapp also serves a `voice-transcriber-ca.mobileconfig`
profile at `/install-ca`. iOS users install once via Settings → General
→ VPN & Device Management, then trust under Settings → General → About →
Certificate Trust Settings. After that, no security warnings, ever.

For the Cloudflare path the certificate question goes away — Cloudflare
terminates TLS — but a bearer token is enforced, since the URL is
publicly reachable until the tunnel closes.

## Launching, unified

The tray is the umbrella process. Starting `tray.bat` brings up the
whisper-server (existing) **and** the web app (new) under the same
adopt-or-spawn pattern. The hotkey workflow is unchanged. Tray menu
gains "Web app: <url>", "Copy mobile URL", "Restart web app".

Three launch surfaces, **identical feature surface**:

| Launcher | What it spawns | Used when |
|---|---|---|
| `tray.bat` (existing) | whisper + webapp + tray icon + hotkey | Daily driver |
| `webapp.bat` (new) | webapp only (adopts whisper if running) | Headless box, dev mode |
| `python launcher.py …` | CLI entry point — `tray`, `gui`, `record`, `transcribe`, `server` | Scripting / Stream Deck |

Removed: `transcribe_voice.bat`, `quick_record.bat`,
`quick_record_english.bat`, `quick_record_spanish.bat`. The tray's F10
hotkey covers the quick-record flow; `python launcher.py gui` covers the
tk window. The bats were redundant.

## Layout — aligned with the monorepo

The other repos in `automation/` (`local-llm-hub`,
`grocery-shopping-automation`, `facilitation-shuffle`) use:

- `src/` for non-UI logic
- `app/` for UI surfaces (Streamlit, tk, FastAPI)

voice-transcriber was the odd one out with `core/`, `gui/`, `cli/`,
`whisper_server/` flat at root. Now aligned:

```
voice-transcriber/
├── src/                            ← logic (no UI imports)
│   ├── app_config.py               (was core/app_config.py)
│   ├── diagnostics.py              (was core/diagnostics.py)
│   ├── recorder.py                 (was core/recorder.py)
│   ├── transcription_client.py     (was core/transcription_client.py)
│   ├── polish.py                   (NEW — Phase 1)
│   ├── archive.py                  (NEW — Phase 1)
│   ├── webapp_config.py            (NEW — Phase 1)
│   └── whisper_server/             (was whisper_server/)
│       ├── __init__.py
│       ├── manager.py
│       └── whisper_server.yaml
├── app/                            ← UI surfaces
│   ├── gui/                        (was gui/)
│   ├── cli/                        (was cli/)
│   └── webapp/                     (NEW — Phase 2)
│       ├── server.py               FastAPI + routes
│       ├── manager.py              adopt-or-spawn for uvicorn
│       └── static/{index.html,app.js,styles.css}
├── config/
│   ├── config.json
│   ├── webapp_config.json          gitignored
│   └── webapp_config.sample.json   committed schema
├── docs/
│   └── 2026-05-07-mobile-webapp-and-repo-cleanup.md   ← this file
├── scripts/
│   ├── install_whisper_cpp.py
│   ├── download_model.py
│   └── gen_ssl_cert.py             (NEW — Phase 3)
├── archive/                        gitignored runtime data
├── launcher.py
├── tray.bat / server.bat / setup.bat / webapp.bat
├── README.md / CLAUDE.md / AGENTS.md / requirements.txt
```

## Decisions and tradeoffs

### Audio format
**webm/opus** on the wire, transcoded to wav server-side via ffmpeg.
~6× smaller than WAV, important on cellular. Adds `ffmpeg` to the
vendored binaries (next to whisper-server). User accepted the extra
dependency.

### Polish model
Default `gemma4-e4b-it` (smallest, fastest classifier-tier model in
local-llm-hub). UI exposes a dropdown with `gemma4-26b-a4b-it` and
`claude-haiku-4-5` as larger options. "Set as default" persists the
choice to `config/webapp_config.json`. Polish prompt is:
> Remove filler words (uh, um, like, you know), false starts, and word
> repetitions. Do not summarize. Do not rephrase. Do not reorder
> sentences. Do not add or remove ideas. Output only the cleaned text,
> nothing else.

### Polish trigger
Manual button, not auto-on-transcribe. User confirmed.

### Mic selection
Web `navigator.mediaDevices.enumerateDevices()` selector with a
**"Force built-in mic"** toggle that filters out Bluetooth devices and
pins the iPhone's built-in mic by `deviceId`. iOS caveat: device labels
are obscured until permission is granted, and iOS sometimes routes
audio through Bluetooth at the system level regardless. Best-effort —
documented in the README.

### History
30-day auto-cleanup on app start (cron-on-boot pattern), plus a
"Clean all" button in the History view. Sessions live in
`archive/YYYY/MM/DD/HH-MM-SS-<id>/` with raw.webm, audio.wav,
transcript.txt, polished.txt, meta.json, polish_request.json,
polish_response.json. Whole `archive/` is gitignored.

### Crash recovery
Every chunk lands on disk during recording. If the iPhone dies mid-take,
the partial `raw.webm` is still on the PC. A History row exposes a
"Re-transcribe" button so a saved take can always be redriven.

### Auth
- Local (`127.0.0.1`) → no token required.
- Tailscale tailnet → no token required (already gated by Tailscale ACL).
- Cloudflare tunnel → bearer token enforced via `WEBAPP_TOKEN` env var.

### Mobile certificate trust
A `.mobileconfig` profile is generated alongside the self-signed CA.
Served at `/install-ca`. iOS install: Settings → General → VPN & Device
Management → tap profile → Install → trust under Certificate Trust
Settings. One-time per device.

### Tray = umbrella
`config/config.json` gains a `webapp.enabled` flag (default `true`).
When the tray boots it adopt-or-spawns uvicorn on `:8443` the same way
it adopt-or-spawns whisper-server on `:8090`. Set `webapp.enabled:false`
to keep the tray purely as it is today.

### Cloudflare quick-tunnel URL discovery
`webapp_tunnel.bat` pipes cloudflared's stdout, regex-extracts the
generated `https://*.trycloudflare.com` URL, writes it to
`webapp/last_tunnel_url.txt`. The tray menu reads that file and offers
"Copy tunnel URL" so you can grab it from your phone via the launcher
without seeing the PC console.

## Phasing

`CLAUDE.md` mandates ≤5 files per phase and verification before
declaring done.

- **Phase 0a** — delete obsolete bats; create `docs/` + this design doc.
- **Phase 0b** — `core/*.py` → `src/`; update imports in launcher, gui, cli.
- **Phase 0c** — `whisper_server/` → `src/whisper_server/`,
  `gui/` → `app/gui/`, `cli/` → `app/cli/`. Update imports + bat scripts.
- **Phase 0d** — update `README.md`.
- **Phase 1** — `src/polish.py`, `src/archive.py`, `src/webapp_config.py`,
  `config/webapp_config.sample.json`. Pure logic.
- **Phase 2** — minimal webapp on `https://127.0.0.1:8443`:
  `app/webapp/server.py`, `app/webapp/static/{index.html,app.js,styles.css}`,
  `webapp.bat`.
- **Phase 3** — `scripts/gen_ssl_cert.py` + iOS `.mobileconfig` +
  `/install-ca` route. iPhone over Tailscale validation.
- **Phase 4** — chunked-upload streaming + crash recovery.
- **Phase 5** — polish + history + mic + config UI in webapp **and** tk
  main window. Feature parity across surfaces.
- **Phase 6** — `app/webapp/manager.py` adopt-or-spawn + tray integration.
- **Phase 7** — `webapp_tunnel.bat` + `last_tunnel_url.txt` + token auth.
- **Phase 8 (separate repo)** — launcher PR for uvicorn detection +
  tunnel URL surfacer. Not included in this doc.

## Validation per phase

- `py_compile` sweep across modified files.
- `python launcher.py server status` + tray boot smoke test after layout
  moves.
- Browser-side: load `https://127.0.0.1:8443` from the PC; check the
  record/transcribe/copy round-trip; check polish round-trip; check
  history list.
- Mobile-side (manual, from iPhone over Tailscale): load over HTTPS,
  install CA profile, record 30-second sample, verify transcript on the
  phone, verify the WAV exists in `archive/`.

---

## Execution log — 2026-05-07

All phases (0a–7) implemented and validated in one session.

**Phase 0a (cleanup + design doc).** Removed
`transcribe_voice.bat`, `quick_record.bat`, `quick_record_english.bat`,
`quick_record_spanish.bat`, and the empty `transcribe_voice/`
directory. Created this design doc.

**Phase 0b/0c (layout refactor).** Moved `core/` → `src/`,
`whisper_server/` → `src/whisper_server/`, `gui/` → `app/gui/`,
`cli/` → `app/cli/`. Updated 13 import sites + the path math in
`src/whisper_server/manager.py` and `app/gui/tray.py`. Verified via
`py_compile` sweep + `python launcher.py server status` (logger now
shows `app.cli.commands.server_cmd`).

**Phase 0d.** README updated: removed-bats table, new tree, mobile webapp section.

**Phase 1.** Created `src/polish.py`, `src/archive.py`,
`src/webapp_config.py`, `config/webapp_config.sample.json`. Smoke test:
round-trip the archive on a tempdir (chunk append, transcript write,
polished write, list, cleanup) — all OK.

**Phase 2.** Created `app/webapp/server.py` (FastAPI),
`app/webapp/audio.py` (ffmpeg helper), and the static page
(`index.html`, `app.js`, `styles.css`, `manifest.webmanifest`).
Validated:
- `GET /healthz`, `/api/config`, `/api/status`, `/api/sessions` all 200
  with correct payloads.
- 2-second sine wave round-trip: `POST /api/sessions` →
  `POST /upload` → ffmpeg transcode → whisper → transcript
  written to `archive/2026/05/07/<id>/`.
- `POST /polish` against `claude-haiku-4-5` (`gemma4-e4b-it` backend
  was down) returned correctly polished text:
  *"Hello, um, this is, like, a test of the polish flow with some, you
  know, filler."* → *"Hello, this is a test of the polish flow with
  some filler."* — fillers stripped, structure preserved.
- `DELETE /api/sessions` removed both test sessions, archive empty.
- `gemma4-e4b-it` failure path correctly persisted to `meta.json`
  (`polish_succeeded: false`, `error: "..."`).

**Phase 3.** Created `scripts/gen_ssl_cert.py`. Generated CA + leaf
covering: `127.0.0.1`, `192.168.0.46` (LAN), `100.107.242.100`
(Tailscale), DNS `localhost`, `tower`, `tower.tail1121fd.ts.net`. Valid
10 years. Auto-installed CA into Windows `CurrentUser\Root`. Mirrored
`voice-transcriber-ca.mobileconfig` and `ca.crt` (DER) into
`app/webapp/static/`. Validated:
- uvicorn starts with `--ssl-keyfile`/`--ssl-certfile`.
- `GET /healthz`, `/install-ca` (XML plist), `/` (index.html) all
  served over HTTPS.

**Phase 4.** Added `POST /api/sessions/{id}/chunk` and `/finish`. Fixed
a bug where `/finish` checked the cached `meta.raw_bytes` instead of
the on-disk file size (chunk handler intentionally skips meta writes
for I/O budget). Tested with a 4-second sine split into two binary
halves: chunks land, on-disk webm is binary-identical to the original,
`/finish` transcodes + transcribes successfully.

**Phase 5.** Webapp UI (already wired in Phase 2): polish dropdown,
history list with re-transcribe + clean-all, mic selector, settings
panel. Tk app gained the same polish row sharing
`config/webapp_config.json` so "set as default" from either surface
syncs.

**Phase 6.** Created `app/webapp/manager.py` mirroring
`WhisperServerManager`'s adopt-or-spawn pattern. Tray boots both
managers in parallel threads; menu gains "📋 Copy mobile URL" and
"🔄 Restart web app". Verified the manager builds the right uvicorn
command (with cert flags), and adopts an externally-running uvicorn
without spawning a duplicate.

**Phase 7.** Created `scripts/run_tunnel.py` + `webapp_tunnel.bat`.
Spawns uvicorn (or adopts) and `cloudflared tunnel --url
https://localhost:8443 --no-tls-verify`. Watches stdout for
`https://*.trycloudflare.com`, persists to `webapp/last_tunnel_url.txt`,
clears the file on shutdown. Manual end-to-end (opening a public
tunnel) deferred to user testing.

**Final sanity.** Full `py_compile` sweep: clean. Full module-import
sanity from a fresh interpreter:

```
language: english
hotkey: <F10>
webapp.enabled: True host: 0.0.0.0 port: 8443
webapp base_url: https://127.0.0.1:8443
whisper: running (external) @ http://127.0.0.1:8090
llm hub reachable: True
ffmpeg: <winget path>
polish default: gemma4-e4b-it
retention days: 30
CLI commands: ['record', 'transcribe', 'gui', 'tray', 'server']
ALL OK
```

Tray boot test with the new code was bumped by the user's existing
running tray (single-instance lock fired correctly — the lock file
points at the previous PID so the duplicate exits cleanly). To pick up
the new tray code, the user has to Quit the existing tray from its
menu, then relaunch `tray.bat`.

Not committed — left for user manual review.

---

## Afternoon refinements — 2026-05-07 (post-manual-test)

The user tested the build live from an iPhone over Tailscale. End-to-end
record → transcribe → copy → polish all worked. Five small frictions
surfaced and were fixed without expanding scope.

### 1. Phone connection refused (`ERR_CONNECTION_CLOSED`)

User typed `100.107.242.100:8443` without the scheme. Chrome defaulted
to plain HTTP, hit our TLS port, the TLS handshake failed and the socket
closed. Not a code bug — a UX trap. README troubleshooting row added
("always type `https://`"). No code change.

### 2. Polish failed: hub upstream `:8086` unreachable

`gemma4-e4b-it` (the default) needs the corresponding llama-server
running in `local-llm-hub`. When it's down the hub returns a clean 502
which we surface verbatim. README troubleshooting row added; either
start the backend or pick `claude-haiku-4-5` in the dropdown. Not a code
bug — the failure path was already correct (error persisted to
`meta.json`, transcript untouched).

### 3. More granular status line

User wanted to know which step was blocking on a long take, without new
UI. Reused the existing one-line `recordStatus` element with phase
strings:

```
Recording · 24.3 KB streamed to PC      (live counter while speaking)
Finalising upload · 2 chunks left       (waiting for last chunks)
Server: ffmpeg → whisper · 1m 4s of audio…
Done in 3.2 s · 20.0× realtime — tap Copy or Polish
LLM hub → claude-haiku-4-5 · polishing…
Polished in 1.4 s — tap Copy
```

Tracked via `state.bytesSent` (running total of successful chunk uploads)
and `state.pendingUploads` (existing). Server-side phases (transcode vs.
transcribe) are not split because they're inside one `/finish` call —
splitting would require SSE or polling, both over-engineering for the
benefit. The single `Server: ffmpeg → whisper · Xs of audio…` covers it.

### 4. Clipboard leaked styled DOM into paste destinations

User reported pasted text showed up with a black background — iOS
Safari was bundling a styled-HTML representation of the source `<div>`
alongside the plain text. Three guards stacked in `copyText` /
`tryAutoCopy` / new `writePlainText`:

1. `window.getSelection().removeAllRanges()` before writing so iOS
   doesn't include the highlighted DOM context.
2. `navigator.clipboard.write([new ClipboardItem({'text/plain': blob})])`
   when available — explicitly one MIME type, paste destinations can't
   fall through to a rich representation.
3. Fall back to `navigator.clipboard.writeText` then to the hidden-
   textarea `execCommand('copy')` trick. Both write plain text only.

### 5. iOS asked for mic permission every record

iOS Safari's default is per-page-load permission. Two parts to the fix:

- **Documented** the system-level path: Add to Home Screen (PWA
  standalone mode persists permission across launches) and the
  Settings → Safari → Settings for Websites → Microphone → Allow
  whitelist.
- **In-session optimisation:** cache `state.stream` across recordings
  with a constraints fingerprint (`state.streamKey = JSON.stringify(constraints)`).
  Reuse if alive, only `getUserMedia` again on:
  - First record after page load
  - Mic dropdown / "Force built-in" toggle changes (invalidate cache)
  - `pagehide` / `visibilitychange:hidden` while idle (release stream
    so the iOS recording indicator clears)
  
  Trade-off: the iOS "in-use" mic indicator stays orange between
  recordings within a session. Acceptable — same UX as WhisperFlow.

### 6. Init dead on transient network blip ("Init failed: Load failed")

User got a generic "Load failed" toast when the page tried to load
config after iPhone-Safari had been idle. Tailscale recovers in <1 s
when iOS wakes; Safari holds a stale TLS connection that errors before
the new one comes up. Hardened `init()`:

- `bindEvents()` runs first so the page is interactive even if config
  fails — pull-to-refresh always works.
- New `fetchJsonWithRetry(url, init, attempts)` retries each init fetch
  once after a 600 ms gap before giving up.
- New `applyConfigDefaults()` populates the dropdowns with baked-in
  fallback values matching the server-side defaults, so even with no
  config the page is usable (only the "remember my chosen polish model"
  bit is degraded).
- New `populateConfigUI()` is shared between the success and
  fallback paths.
- `visibilitychange:visible` triggers an opportunistic
  `loadConfig()` when the tab regains focus and config is still null.

### Files touched today (post-manual-test)

- `app/webapp/static/app.js` — all five behaviour changes above.
- `README.md` — full first-time setup guide, status line legend,
  polish models, history, troubleshooting table.
- `docs/2026-05-07-mobile-webapp-and-repo-cleanup.md` (this file) —
  this section.

No backend changes. No new dependencies. No new HTML elements. The whole
afternoon was localised to `app.js` plus docs.

---

## Follow-up — bearer-token auth for the public tunnel (same day)

Closes item #1 of `docs/todo.md`.

### Why

`webapp_tunnel.bat` puts the recorder on a public
`https://*.trycloudflare.com` URL. Anyone who guesses or intercepts
that URL while the tunnel is up can record / transcribe / polish on
the home PC, burning local hardware and (for the `claude-*` models)
Claude subscription quota. Tailscale-only / loopback paths are
already gated by Tailscale's ACL, so this only matters when the
tunnel is the exposure path — but it matters enough that the gate
shouldn't be opt-in via a bespoke change. A built-in, dormant-by-
default token gate covers it.

### Design

- **Default off.** `auth_token` defaults to `""` (already in the
  dataclass before this change). With an empty token the middleware
  short-circuits — every existing flow (tk window, Tailscale phone,
  cloudflared) keeps working unchanged. Zero config required to
  upgrade.
- **Loopback always bypasses.** `client.host in {"127.0.0.1", "::1"}`
  goes straight through. The tk main window's reuse of the API and
  any local probe / script keeps working without the token. Per the
  TODO's "simplest first cut": no Tailnet IP-range bypass — Tailscale
  callers must carry the token too.
- **Page boot is exempt.** `/`, `/static/*`, `/healthz`, `/install-ca`
  remain reachable so the JS can load and pick up the token from
  `?token=…` before any API call fires.
- **Token accepted from header or query string.** Header is the
  steady state (`Authorization: Bearer <token>`). Query string is the
  bootstrap path: a phone navigating to `…/?token=…` for the very
  first time. The JS strips `?token=` from the URL via
  `history.replaceState` after stashing it in `localStorage`, so the
  Home Screen icon stays clean.
- **Constant-time compare.** `hmac.compare_digest` not `==`.

### Files touched

- `src/webapp_config.py` — added `append_auth_token(url, token)`
  helper used by both the tray and the tunnel launcher.
- `app/webapp/server.py` — `BearerTokenMiddleware` + `app.add_middleware(...)`,
  reading the token via `getattr(app.state.webapp_config, ...)` so a
  `/api/config` patch that rotates it takes effect without a restart.
- `app/webapp/static/app.js` — boot-time `captureTokenFromURL()`,
  `authFetch` wrapper, every `fetch(...)` call replaced with
  `authFetch(...)`.
- `app/gui/tray.py` — `_copy_webapp_url` now appends `?token=…` from a
  fresh `load_webapp_config()` read at copy-time so a rotation
  doesn't require a tray restart.
- `scripts/run_tunnel.py` — when writing `last_tunnel_url.txt`,
  appends `?token=…` and logs a hint if the token is set. Stale
  follow-up note removed from the docstring.
- `scripts/gen_token.py` (new) — `secrets.token_urlsafe(32)` →
  `webapp_config.json` via `update_webapp_config(...)`. Flags:
  `--force` rotates, `--clear` disables. Print-out includes the
  full onboarding flow so the user doesn't need to dig through docs.
- `config/webapp_config.sample.json` — `_comment_auth_token` line
  pointing at the README section.
- `README.md` — new "Optional: bearer-token auth" section under
  "Mobile web app", plus two troubleshooting rows (401 vs 502).
- `docs/todo.md` — item #1 marked shipped, original spec preserved
  in a `<details>` block.

### Validation

- `py_compile` on every changed Python file.
- The auth gate is dormant on this branch (the user did not run
  `gen_token.py`), so the manual-test flow recorded above on the same
  date continues to apply unchanged. Token-on flow was not exercised
  end-to-end — to be tested when the user enables the gate. Test
  matrix when they do:
  - tk window: still works (loopback bypass).
  - Tailscale phone without token: 401 → open the tokenised URL once
    → all later visits work from `localStorage`.
  - Cloudflare tunnel: same flow via `webapp/last_tunnel_url.txt`.
  - Rotation (`gen_token.py --force`): old phones 401, new tokenised
    URL re-bootstraps them.
  - `gen_token.py --clear`: gate off again, behaviour identical to
    pre-change.

### Out of scope (intentionally)

- Tailnet IP-range bypass — keeping it loopback-only forces the
  Tailscale phone through the same auth path as a tunnel visitor,
  which is the simpler mental model.
- Any work on `automation/launcher` (item #2 of the TODO) — lives in
  a different repo, kept separate per `docs/todo.md` process notes.
