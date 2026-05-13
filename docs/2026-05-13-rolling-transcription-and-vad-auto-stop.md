# Rolling transcription + VAD auto-stop on the webapp

**Date:** 2026-05-13
**Issue:** [#5 — Latency collapse](https://github.com/ferraroroberto/voice-transcriber/issues/5) (Pillar 1 + Pillar 3 only; Pillar 2 dropped intentionally)
**Branch:** `feat/latency-collapse-rolling-transcription`

## What changed

The webapp now does **rolling transcription** while you're still talking
and can **auto-stop on silence** when you flip the toggle. The
stop-to-clipboard wall-clock isn't dramatically faster on a manual
Stop — whisper still runs one final pass over any new bytes since the
last partial — but the transcript box fills in live as you speak, so
the take never *feels* like a black box. With auto-stop on, the rolling
worker has time to catch up during the silence window and the final
`/finish` pass short-circuits, which is where the user-visible latency
actually collapses.

## Files modified

```
src/webapp_config.py                     +52  new flags, validation
config/webapp_config.sample.json          +5  documented defaults
config/webapp_config.json                 +3  local config bumped
app/webapp/partial_worker.py             +230 new module
app/webapp/server.py                     +146 SSE endpoint + worker registry
app/webapp/static/index.html              +5  Auto-stop toggle in Settings
app/webapp/static/app.js                 +190 EventSource consumer + VAD
docs/2026-05-13-rolling-transcription-and-vad-auto-stop.md  this file
```

## Architecture

```
record start
   │
   ▼
POST /api/sessions          ─►  create session folder
GET  /api/sessions/{id}/events?token=…   (SSE, EventSource)
                                ▲
chunk every 1 s ────────────────┤
POST /api/sessions/{id}/chunk   │  marks worker dirty
                                │
                                │  PartialWorker (per session):
                                │    • debounces partial_interval_seconds (2.0 s)
                                │    • snapshots raw.webm → partial_raw.webm
                                │    • ffmpeg → partial.wav
                                │    • POST whisper → text
                                │    • broadcasts "partial" SSE event
                                │
stop                            │
   │                            │
   ▼                            │
POST /api/sessions/{id}/finish ─┘
   │
   ├─ if last partial covers full audio → return cached partial
   ├─ else → one final whisper pass (legacy path)
   └─ either way → broadcast "final" SSE event, shut down worker
```

## Config knobs (`config/webapp_config.json`)

| Key | Default | Effect |
|---|---|---|
| `partial_interval_seconds` | `2.0` | Re-run whisper every N seconds while recording. `0` disables the rolling worker entirely. |
| `vad_auto_stop_enabled` | `false` | When `true`, the client auto-stops after `auto_stop_silence_ms` of silence. |
| `auto_stop_silence_ms` | `1500` | How long continuous silence has to last before Stop fires. A 500 ms "keep talking to cancel" banner appears first. |

Both VAD knobs are also surfaced in **⚙️ Settings** on the webapp.
The Auto-stop toggle takes effect immediately on flip (no Save tap
needed) — same UX pattern as the existing Translate / Append /
Incognito toggles. **💾 Save** persists the choice as the default for
fresh page loads.

## What was dropped from the original plan

- **Pillar 2 (speculative polish)** — tried it, didn't keep it. Even
  off-by-default the surface area wasn't worth the maintenance — the
  polished-text box ended up empty in practice (the cancel-and-retry
  chain rarely converged before Stop on typical takes), so all it
  bought us was extra LLM calls and code paths to reason about. Server
  worker has no polish hook; client has no `polish_partial` SSE
  handler. The existing manual ✨ Polish button on the page is
  unchanged and remains the only polish path.
- **Phase C (F8 hotkey through the rolling pipe)** — explicit decision
  not to wire it. The desktop hotkey has no UI to show partials, so
  the only win would be ~1-3 s of paste latency. The cost is making F8
  dependent on uvicorn being up (today F8 works even if the webapp is
  dead). If a cleaner version is ever wanted, the right refactor is
  extracting a `RollingTranscriber` core into `src/` that both surfaces
  consume directly, not routing the tray through HTTP-on-loopback.

## VAD threshold tuning

The client-side VAD uses the existing `AnalyserNode` energy floor —
`max(|sample - 128|)` per ~10 ms frame, against a hard-coded
`VAD_LOUDNESS_THRESHOLD = 15` in `app.js`. That maps to roughly
-18.5 dBFS peak. Selection history:

- `6` (initial) — too tight; quiet-room floor with mic AGC engaged
  often peaks at 5-10, so the silence accumulator never advanced.
- `15` (current) — sits above the Elgato Wave XLR's idle preamp hiss
  but well below speech peaks (~30-60).

A live `🎙️ VAD peak=N (silence trips ≤ 15)` readout in the status
line lets the user see exactly what their mic floor is, in case the
threshold needs per-mic tuning later. Promoting it to a config knob is
trivial if it comes up.

## Validation

```bat
.venv\Scripts\python.exe -m pytest --no-header -q
:: 262 passed, 1 skipped

npx vitest run app/webapp/static/__tests__/
:: 10 passed

curl -k https://127.0.0.1:8443/healthz
:: {"ok":true,"service":"voice-transcriber-webapp"}

curl -k https://127.0.0.1:8443/api/config | jq '{rolling_transcription_enabled, vad_auto_stop_enabled}'
:: { "rolling_transcription_enabled": true, "vad_auto_stop_enabled": true }
```

Manual smoke (in a browser):

1. Hard-reload, tap RECORD, speak 10 s.
2. Within ~2 s the transcript box fills in live; status shows
   `Recording · partial v1 · …`, then v2, v3.
3. Tap Stop. If the last partial covered the full audio, the response
   carries `from_partial: true` and the transcript settles in ~100 ms.
4. With Auto-stop on: stop speaking; the status counts
   `🤫 silence 320 ms / 1500 ms` → `640` → ... → grace banner → Stop.
