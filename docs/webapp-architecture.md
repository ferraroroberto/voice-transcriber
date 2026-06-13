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
- Tiny: uvicorn + FastAPI + a single static page. No client-side
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
  `?token=…` before any API call fires.
- **Token accepted from header or query string.** Header is the steady
  state (`Authorization: Bearer <token>`). Query string is the bootstrap
  path: a phone navigating to `…/?token=…` for the very first time. The
  JS strips `?token=` from the URL via `history.replaceState` after
  stashing it in `localStorage`, so the Home Screen icon stays clean.
- **Constant-time compare.** `hmac.compare_digest`, not `==`.

`scripts/gen_token.py` generates / rotates / clears the token
(`secrets.token_urlsafe(32)` → `webapp_config.json`).
