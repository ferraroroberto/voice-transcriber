# Mobile web app

A FastAPI web interface lives at `https://<host>:8443`. It is a
WhisperFlow-equivalent for the iPhone (and any other browser): one big
button to record, audio streams to the PC as you speak, the transcript
comes back, one tap copies it. An optional second tap polishes through
the local LLM hub (filler-word removal, no rephrasing).

## Where to launch from, where to reach it

| Where you are | What to launch | How to reach it |
|---|---|---|
| Sitting at the PC | `tray.bat` (or already running) | `https://127.0.0.1:8443`, or tray → **📋 Copy local URL** |
| Anywhere else (phone, work PC, hotel Wi-Fi) | `tray.bat` on the home PC | Your bookmarked Cloudflare URL, e.g. `https://voice.<your-domain>` — tray → **📋 Copy Cloudflare URL** |
| Headless box / dev | `webapp.bat` (no tunnel) or `webapp_tunnel_named.bat` (with tunnel) | `https://127.0.0.1:8443` / your Cloudflare URL |

The tray launches whisper-server, the webapp, and (if
`webapp/cloudflared.yml` exists) cloudflared too. One launch covers
local + remote in a single step. To opt the webapp out entirely, set
`"webapp": {"enabled": false}` in `config/config.json`. To skip the
tunnel, just don't create `webapp/cloudflared.yml` (it's gitignored
anyway).

## First-time setup

### 1. PC: generate the local HTTPS cert (one-time)

The webapp serves HTTPS on the loopback so the local browser still
sees a secure context (needed for `getUserMedia` when you record from
the home PC). Cloudflare terminates TLS at the edge for everything
else — phones and remote PCs never see this cert.

```powershell
& .\.venv\Scripts\python.exe scripts\gen_ssl_cert.py
```

This writes `webapp/certificates/{ca.pem,cert.pem,key.pem}` and
installs the CA into the Windows user trust store via `certutil`
(no admin required) so the local browser shows a green padlock.
Valid 10 years. Restart the tray (right-click → Quit, then
`tray.bat`) so uvicorn picks up the cert.

### 2. iPhone: install the webapp as a Home Screen icon

Open the **Cloudflare URL** in Safari (e.g.
`https://voice.<your-domain>`), then **share sheet → Add to Home
Screen** → name it "Voice". Launch from that icon from now on —
iOS treats it as a standalone app and persists mic permission
across launches.

> iOS aggressively caches a Home Screen icon's artwork. An old icon
> that still shows a "W" letter instead of the mic glyph has to be
> removed (**long-press → Remove Bookmark**) and re-added via the
> share sheet to pick up the current artwork.

> First-time visit on a new device: open the tokenised URL the tray
> copies via **📋 Copy Cloudflare URL** (it includes `?token=…` if
> you've enabled bearer-token auth). The page stashes the token in
> `localStorage` and strips it from the visible URL. From then on
> just open the icon — nothing to type.

Within a single page session the app keeps the mic stream alive
between recordings, so back-to-back records never re-prompt.

## Daily use

Open the home-screen icon → tap the big blue **Record** circle (it turns
red while the mic is live) → speak → tap **Stop**. The text appears in
the **Text** card, auto-copied to the clipboard (its **Copy** button
briefly reads *Copied* to confirm), ready to paste anywhere. Under the
text, a meta line gives the take's time, word count and length.

**Polish** sits in the Text card's verb row, after Copy. A tap runs the
text through the local LLM hub (model and style from Settings), switches
the card to its **Polished** view and auto-copies the result. A
**Transcript · Polished** switch appears in the card head once a polish
exists. Both versions are editable and keep their edits when you switch,
and **Copy** always takes the version on screen. Edits are sent to the
server on the next polish call so History matches what's on screen. When
the hub is unreachable the button reads *Polish unavailable*.

You can also skip recording entirely: paste any text into the Text card
and tap **Polish** — a text-only session is created and shows up in
History alongside dictated takes. If you only want to save the pasted
text for later (no polish yet), tap **Save**, which appears only for text
History doesn't hold yet. It creates a text-only session in History so
you can polish or re-copy it from there, exactly as if you had dictated
it. **Clear**, at the end of the verb row, clears the text and the
current session so the next record starts fresh.

### Append mode

An **Append** toggle pill sits under the record button. With it on, every new
take is glued onto the existing transcript with a blank-line
separator instead of replacing it — useful when you're moving
between locations or apps and want to build up one big transcript
across multiple records before polishing. The toggle is ephemeral
(off on every fresh page load) and exists on every surface: the
toggle pill in the webapp, **➕ Append mode** menu item in the tray,
**➕ Append** checkbox on the *Last transcription* row in the tk
window. The tray menu and tk checkbox share one flag so toggling
either keeps both in sync; the webapp toggle is independent.

### Incognito mode

An **Incognito** toggle pill under the record button. While it is on (blue),
the **next recording** is flagged `incognito=true` server-side and
never appears in the History list — useful for taking a private
note that shouldn't sit on disk for the 30-day retention window.
The session still works normally during its lifetime (record,
transcribe, polish, copy); the moment you hit **Clear** or
start the next recording, the client sends `DELETE
/api/sessions/{id}` so the folder is gone.

Ephemeral on the client (off on every fresh page load); webapp
only by design — the tk window and tray-hotkey flows don't write
anything to `archive/`, so every desktop take is already
effectively incognito and there's no history list to hide from.

### Tk window controls

The tk main window mirrors the webapp's three header tools as far
as the desktop flow allows:

- **➕ Append** — same flag as the tray menu (toggling either keeps
  the other in sync). Each new take glues onto the current one
  with a blank-line separator.
- **🧽 Reset** — clears the *Last transcription* + *Polished*
  panels and the in-memory slot, so the next take starts on a
  clean page without restarting the app.
- **Editable transcript** — the *Last transcription* box accepts
  edits; corrections flow back into the slot so **✨ Polish** runs
  against your edited text.
- **Force built-in mic (skip Bluetooth)** — checkbox under the
  *Mic* combobox. Only effective when the combo is at *System
  default*; biases device selection toward inputs whose name
  contains *realtek*, *built-in*, or *internal*. Default seeded
  from `force_builtin_mic_default` in `webapp_config.json`.

### Silence skip

Whisper hallucinates plausible-sounding text on near-silent input
("Thanks for watching!", "[Music]", a single "you", etc.). Every take
runs through a loudness gate before transcription: clips with peak
RMS below `silence_dbfs_threshold` (default `-50` dBFS, configurable
in `config/webapp_config.json`) skip whisper entirely and report
`Empty audio (X.X dBFS) — skipped` in the status line, with no
transcript written. False negatives (rejecting actual speech) are
worse than the occasional hallucination slipping through, so the
default is conservative — lower it (e.g. `-55`) if you whisper a lot,
raise it (e.g. `-45`) if hallucinations get through anyway.

While recording, audio is streamed to the PC every second and persisted
to `archive/YYYY/MM/DD/HH-MM-SS-<id>/raw.webm`. If your phone dies or
the connection drops mid-record, the partial recording is still on the
PC — a take's **⋮** sheet in the **History** view offers *Redo transcription*,
which replays whisper on any saved take.

### Quiet-environment gain boost

A distinct control from silence skip above: **Boost quiet audio** in
**Settings › Sound** (with *Boost amount*) amplifies captured audio by a configurable
amount (default `12` dB, range `0`–`24`) before it reaches whisper —
helping a quiet mic or a quiet room transcribe more reliably. It runs
*after* the silence gate, so a genuinely silent take still gets skipped
on its original (un-boosted) loudness — the two settings are orthogonal
and both persist in `config/webapp_config.json` (`gain_boost_enabled`,
`gain_boost_db`). The tk main window mirrors the same toggle + dB
spinbox, saving to the same shared file; the tray's hotkey-driven
recordings pick up whichever value is currently saved, with no
dedicated tray menu entry (same pattern as the silence threshold).

### Text size

The PWA locks the viewport zoom, so **Settings** opens with a **Text size**
control — **Small / Default / Large** — as the way to enlarge text (it scales
the root font size to 93.75% / 100% / 112.5%, and every rem-based type role
follows; the nav, rows and hit targets keep their pixel geometry). It applies
the moment you tap it and is remembered **per device** in the browser
(`voice-transcriber.textsize`), not in `webapp_config.json`, so the phone and
the PC each keep their own. A pre-paint snippet in `<head>` restores it before
first paint, so the page never reflows. It is webapp-only by design: the tk
window and tray have no viewport zoom lock to escape.

### Rolling transcription

Whisper runs every ~2 s on the audio you've already streamed, so the
Text card fills in live while you keep talking — no more black
box between "tap Stop" and "see text". The status line shows
`Recording · partial v1 · …`, then `v2`, `v3`, as each pass lands.
When the final `/finish` pass arrives the Text card gets
replaced wholesale with the canonical version (whisper is a
sliding-context model, so consecutive passes can disagree on earlier
words as the take grows; the *final* pass wins).

Config knobs in `config/webapp_config.json`:

- `partial_interval_seconds` — how often to re-run whisper while you
  talk (default `2.0`; set to `0` to disable rolling transcription
  entirely and fall back to the one-shot-on-stop behaviour).

### Auto-stop on silence

A **Stop after silence** toggle in **Settings › Sound** —
when on, the page watches the mic energy floor and fires Stop after
*Silence length* of continuous near-silence (`auto_stop_silence_ms`,
default 1.5 s).
A 500 ms "keep talking to cancel" banner appears first so a thinking
pause doesn't cut you off. The toggle takes effect immediately on
flip and is saved at once as the default for fresh page loads.

The detector runs on the existing `AnalyserNode` energy floor — no
extra dependencies, no ONNX. A live `VAD peak=N (silence trips
≤ 15)` readout in the status line lets you see exactly what your mic
floor is, in case the threshold ever needs tuning.

### Switching apps while recording

Mobile browsers can't keep a web page recording in the background.
iOS suspends the page and revokes the mic the moment you switch apps
or lock the screen, and there is no web API to capture audio in the
background. (Android Chrome *can* keep a mic-capturing tab alive, but
relying on that would make the two platforms behave differently.)

So the webapp does the next best thing, symmetrically on both: the
moment you background it mid-record, the take is **finalised** — the
audio streamed so far is transcribed and saved to History instead of
silently lost. When you come back, a **Continue this text** button appears
beside a smaller record button — one tap starts a new take that continues
the same transcript (it force-appends, whatever the **Append** toggle
is set to), so the seam across the app-switch is invisible. The plain
**Record** button still starts a fresh take. Worst case — the page is
discarded before the finalise lands — the streamed chunks are still on
the PC, recoverable via History → **Redo**.

True background recording on iPhone is only possible from a native
app (for example a Custom Keyboard Extension).

## What the status line tells you

The line under the record button reports exactly which step is running
so a long take never feels stuck:

| Phase | Message |
|---|---|
| Live recording | `Recording · 24.3 KB streamed to PC` (live byte counter) |
| Rolling partial landed | `Recording · partial v3 · 41.2 KB streamed` |
| Auto-stop armed | `Silence 640 ms / 1500 ms` |
| Auto-stop firing | `Auto-stop on silence — keep talking to cancel…` |
| Stop, chunks pending | `Finalising upload · 2 chunks left` |
| Server processing | `Server: ffmpeg → whisper · 1m 4s of audio…` |
| Done | `Done in 3.2 s · 20.0× realtime — tap Copy or Polish` |
| Polish in flight | `LLM hub → gemini_flash · polishing…` |
| Polish done | `Polished in 1.4 s — tap Copy` |

