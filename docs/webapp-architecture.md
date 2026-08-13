# Mobile webapp — architecture & design record

Durable design rationale for the mobile-first FastAPI web interface and the
`src/` (logic) vs `app/` (UI) layout. This is a *reference* record — the
"why", not a changelog. For what shipped when, see `git log` and the PRs.

## Goal

A WhisperFlow-equivalent on the iPhone: open a web app, tap one big
button, dictate, get the transcript copied to the clipboard, optionally
polish it through the local LLM hub. **Never lose a recording**, even
if the connection drops mid-take. Same flow available from any other
device with a mic + browser.

The app is mobile-first but works identically from a desktop browser
sitting at the PC (loopback) or from anywhere else in the world, over
the same Cloudflare tunnel.

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
- Tiny: uvicorn + FastAPI + a single static page. No client-side
  framework.

Streamlit stays a great fit for the form-heavy apps in the monorepo
(`grocery-shopping-automation`, `facilitation-shuffle`). It's the wrong
tool for a single huge-button voice recorder.

## Network exposure

One context per launcher, both live from the same `tray.bat`:

| Context | Path | Reason |
|---|---|---|
| Sitting at the PC | Loopback (`https://127.0.0.1:8443`) | No tunnel needed; a local self-signed cert gives Safari/Chrome the secure context `getUserMedia` requires. |
| Anywhere else (phone, work PC, hotel Wi-Fi) | Cloudflare named tunnel | Public HTTPS URL, no firewall changes, real edge TLS. Same pattern as `facilitation-shuffle/launch_server.bat`. |

Tailscale support (a third, direct-to-`:8443` tunnel context) was
dropped in favor of Cloudflare-only remote access (`2611178`,
2026-05-09) — the tray no longer offers a "Copy Tailscale URL" entry,
and the README's routing table only documents the loopback and
Cloudflare rows above. The PC may still run Tailscale for unrelated
reasons, and a tailnet peer that knows the machine's Tailscale IP can
still technically reach `:8443` directly — the bearer-token middleware
(below) accounts for that as a defense-in-depth case — but it is not a
documented or promoted way to reach this app.

**HTTPS is mandatory** for the loopback context because iOS/desktop
Safari refuses `getUserMedia` over plain HTTP. `scripts/gen_ssl_cert.py`
provisions a self-signed CA + leaf cert used *only* for that loopback
endpoint; the webapp also serves a `voice-transcriber-ca.mobileconfig`
profile at `/install-ca` for anyone trusting that cert manually.

The `/install-ca` link was removed from the Settings pane in issue #109
— phones reach the app over Cloudflare, which terminates real TLS at
the edge, so they never need the local-CA trust dance at all. The
endpoint itself still serves, for legacy direct-`:8443` setups.

For the Cloudflare path the certificate question goes away entirely —
Cloudflare terminates TLS — but a bearer token is enforced, since the
URL is publicly reachable until the tunnel closes.

## Launching, unified

The tray is the umbrella process. Starting `tray.bat` brings up the
whisper-server **and** the web app under the same adopt-or-spawn pattern.
The hotkey workflow is unchanged. Tray menu gains "Web app: <url>",
"Copy local URL", "Copy Cloudflare URL", "Restart web app".

Three launch surfaces, **identical feature surface**:

| Launcher | What it spawns | Used when |
|---|---|---|
| `tray.bat` | whisper + webapp + tray icon + hotkey | Daily driver |
| `webapp.bat` | webapp only (adopts whisper if running) | Headless box, dev mode |
| `python launcher.py …` | CLI entry point — `tray`, `gui`, `record`, `transcribe`, `server` | Scripting / Stream Deck |

## Layout — aligned with the monorepo

The other repos in `automation/` (`local-llm-hub`,
`grocery-shopping-automation`, `facilitation-shuffle`) use:

- `src/` for non-UI logic (no UI imports)
- `app/` for UI surfaces (Streamlit, tk, FastAPI)

voice-transcriber was the odd one out with `core/`, `gui/`, `cli/`,
`whisper_server/` flat at root; it now follows the same `src/` + `app/`
split. The canonical, up-to-date directory tree lives in `README.md` —
this section records *why* the split exists, not the current file list.

## Decisions and tradeoffs

### Audio format
**webm/opus** on the wire, transcoded to wav server-side via ffmpeg.
~6× smaller than WAV, important on cellular. Adds `ffmpeg` to the
vendored binaries (next to whisper-server).

### Polish model
The available alias set and default are defined in `config/webapp_config.sample.json` — that file is the single source of truth, so this section cannot drift. "Set as default" persists the choice to `config/webapp_config.json`. The polish prompt removes filler words, false starts, and word repetitions only — no summarizing, rephrasing, reordering, or adding/removing ideas.

### Polish trigger
Manual button, not auto-on-transcribe.

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

### Tray = umbrella
`config/config.json` gains a `webapp.enabled` flag (default `true`).
When the tray boots it adopt-or-spawns uvicorn on `:8443` the same way
it adopt-or-spawns whisper-server on `:8090`. Set `webapp.enabled:false`
to keep the tray purely as it is today.

### Cloudflare named-tunnel URL discovery
`scripts/run_named_tunnel.py` reads the hostname from the tunnel's YAML
config and writes `https://<hostname>` to `webapp/last_tunnel_url.txt`
before cloudflared even starts (the URL is fixed, not discovered from
stdout). The tray menu reads that file and offers "📋 Copy Cloudflare URL"
so you can grab it from your phone via the launcher without seeing the PC
console.

## Bearer-token auth for the public tunnel

`webapp_tunnel_named.bat` puts the recorder on a persistent
`https://<your-domain>` URL. Anyone who guesses or intercepts that
URL while the tunnel is up can record / transcribe / polish on the home
PC, burning local hardware and (for the `claude-*` models) Claude
subscription quota. Tailscale-only / loopback paths are already gated by
Tailscale's ACL, so this only matters when the tunnel is the exposure
path — but a built-in, dormant-by-default token gate covers it.

- **Default off.** `auth_token` defaults to `""`. With an empty token the
  middleware short-circuits — every existing flow (tk window, Tailscale
  phone, cloudflared) keeps working unchanged. Zero config required.
- **Loopback always bypasses.** `client.host in {"127.0.0.1", "::1"}`
  goes straight through, so the tk main window's reuse of the API and any
  local probe / script keeps working without the token. No Tailnet
  IP-range bypass — Tailscale callers must carry the token too, which is
  the simpler mental model.
- **Page boot is exempt.** `/`, `/static/*`, `/healthz`, `/install-ca`
  remain reachable so the JS can load and pick up the token from
  `?token=…` before any API call fires. `/api/login` is also exempt — a
  device with no token yet has to be able to reach it to trade a
  password for one — and `/api/version` is exempt so the build line
  renders before the visitor has authenticated.
- **Token accepted from header or query string.** Header is the steady
  state (`Authorization: Bearer <token>`). Query string is the bootstrap
  path: a phone navigating to `…/?token=…` for the very first time. The
  JS strips `?token=` from the URL via `history.replaceState` after
  stashing it in `localStorage`, so the Home Screen icon stays clean.
- **Constant-time compare.** `hmac.compare_digest`, not `==`.

`scripts/gen_token.py` generates / rotates / clears the token
(`secrets.token_urlsafe(32)` → `webapp_config.json`).

### Password gate (companion to the token)

A long tokenised URL is awkward to paste on every fresh device, and on
iOS PWAs whose `localStorage` is partitioned from Safari's main jar the
token sometimes doesn't carry over. `scripts/set_password.py` sets or
clears a short password (`config/webapp_config.json`'s `auth_password`)
that lets a device trade it for the bearer token instead of needing the
tokenised URL:

- **`POST /api/login` swaps a password for the bearer token.** It is
  deliberately outside the bearer gate (`app/webapp/routers/auth.py`) —
  a device with no token has to be able to reach it — so it is the one
  endpoint a caller can exercise repeatedly without presenting a
  credential.
- **`AttemptLimiter` bounds that exposure.** A small free allowance
  (5 attempts) covers a fat-fingered human on a phone keyboard; past
  that, each further rejected attempt from the same client waits out a
  doubling delay (2s → 300s ceiling), so an unbounded guess rate never
  materialises without ever locking the owner out permanently.
- **Failed and throttled attempts are logged** with the requesting
  client IP to `webapp/auth.log`, in addition to the normal server log,
  so suspicious access is visible without scrolling full server logs.
- **The password is a UX wrapper, not a second secret store.** The
  bearer token must already be set (`gen_token.py`) for the password to
  do anything — login just hands the existing token back once the
  password checks out via `hmac.compare_digest`.
