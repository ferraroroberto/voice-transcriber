# Latency collapse — make the polished text already be there

> **Status:** plan only, no code yet.
> **Scope:** this repo end-to-end (server + webapp + tk window + tray hotkey) plus a tiny addition on the local-llm-hub side (cancellable polish).
> **Goal:** drive the wall-clock time from "user stops talking" to "polished text on clipboard, at the caret" toward **<1 s on typical takes**, down from today's ~4–8 s. Same model, same hardware, same accuracy — purely a pipelining + scheduling problem.

---

## TL;DR

Today the pipeline is strictly sequential and the user pays for every stage:

```
[ speak ] → [ stop ] → [ finalise upload ] → [ transcode ] → [ whisper ] → [ click Polish ] → [ LLM polish ] → [ copy ]
   live       ~0 s         ~0.1–0.5 s         0.1–0.3 s     0.5–3 s         human RT          0.8–2 s          0
```

For a 30 s take on the tower with `large-v3-turbo` + `agentic_light`, the user sits looking at a status line for ~3–6 s. On the phone via Cloudflare, add ~1 s of round-trip and ~1 s of "tap polish on a small screen".

The fix is *not* a faster model. The fix is to **overlap the stages with the live recording window**, finishing all heavy work before the user even hits stop. Three independent levers, each shippable on its own, multiplicative when combined:

1. **Rolling transcription** — partial transcripts every ~2 s of audio while you keep talking.
2. **Speculative polish** — debounced LLM call on the rolling transcript, cancelled-and-retried as new words arrive; the *last* one wins.
3. **VAD-driven auto-stop** — silence ≥ N seconds finalises the take so the user never has to reach for Stop on long dictations.

End state: tap record → speak → fall silent → ~600 ms later the polished text is on the clipboard (and pasted at the caret on the hotkey path). The user's perception flips from "wait for the tool" to "the tool was waiting for me".

**Difficulty: 3/5.** No new infrastructure, no new dependencies. The hub already serves polish; whisper.cpp already supports the bits we need; the webapp already streams 1 s chunks to disk (`recorder.start(1000)` in `app/webapp/static/app.js`, `/api/sessions/{id}/chunk` in `app/webapp/server.py`). What's new is the scheduling logic around them.

**Why this is the 10x lever for ~2x effort:** it's invisible until you ship it, then every single take feels different. For a tool the user hits dozens of times a day, a 4-second cut on the critical path compounds harder than any new feature.

---

## What today's pipeline actually looks like (verified, not guessed)

Sources read while drafting this plan:

- `src/recorder.py` — desktop capture into one in-memory buffer; no incremental output.
- `src/transcription_client.py` — single POST of a complete WAV to `/v1/audio/transcriptions`.
- `src/polish.py` — single non-streaming POST to the hub's `/v1/messages`.
- `app/webapp/server.py` — `/upload` is single-shot; `/chunk` + `/finish` is the streaming receiver but transcribes only **once**, in `_transcribe_session_payload`, after `/finish`.
- `app/webapp/static/app.js` — MediaRecorder with 1 s timeslice, each chunk POSTed to `/chunk` as it arrives. Good plumbing, no early transcription.
- `app/gui/tray.py` — hotkey recorder is fully buffered: record → silence check → POST → transcribe → paste. No partials.
- `vendor/whisper.cpp/` — bundle includes `whisper-stream.exe`, `whisper-vad-speech-segments.exe`, `test-vad-full.exe`. VAD and streaming are first-class in the binary we already ship.

So: the **chunking pipe already exists** on the web side. We're not building it — we're extending it to actually do work as chunks arrive, and adding a parallel pipe on the desktop side.

---

## The three pillars

### Pillar 1 — Rolling transcription

**Idea.** Every ~2 s of accumulated audio, transcribe the **whole take so far** with whisper. Send the partial transcript back to the client. The final result on stop is just the last partial, no re-run needed.

**Why "whole take" rather than chunk-by-chunk.** Whisper is a sliding-context model: transcribing each 2 s chunk independently is brittle (word boundaries, punctuation, no cross-chunk context). Re-transcribing the whole take is cheap on `large-v3-turbo` with CUDA — about 20× realtime — so a 30 s take re-runs in ~1.5 s per pass. By the time you stop, the last pass is already on its way; the *new* whisper call on stop is usually skippable.

**Implementation shape on the server side.**

A new background coroutine per active session, started by `/api/sessions` (or implicitly on first chunk), that:

1. Watches `raw.webm` size and a "dirty" flag the chunk endpoint sets.
2. On a tick (every ~1.5–2 s, configurable), if dirty and not currently transcribing, transcodes the *current* raw file to a working WAV and runs whisper.
3. Persists the partial to a new `session.partial_transcript` and bumps a monotonic `partial_version`.
4. Clears the dirty flag; goes back to watching.

Concurrency: one in-flight whisper call per session, never two. If the user keeps talking while whisper is busy, the next pass picks up whatever's on disk then.

New endpoints (small additions to `app/webapp/server.py`):

- `GET  /api/sessions/{id}/partial` → `{partial_version, transcript, finalised: bool}`. Cheap, polled by the client.
- Or — preferred — `GET /api/sessions/{id}/events` as Server-Sent Events: `partial`, `polish_partial`, `final`. SSE works over Cloudflare tunnels without any special config, no websocket upgrade dance.
- `/finish` returns immediately if a partial already covers the full audio (`raw_bytes == bytes_at_last_partial`); otherwise runs one final pass.

**Client changes (webapp).** Open the SSE stream on record start, render `partial` events into the transcript box live, replace with the `final` on stop. Show a tiny "•" pulse while a partial is in flight so the user knows it's working.

**Desktop side.** `src/recorder.py` is fully buffered; for parity we add a second mode in the tk window + tray that mirrors the webapp's chunked flow: capture into `sounddevice`, every 1 s flush a chunk to the running webapp's session endpoints via loopback. The tray's hotkey path keeps a parallel SSE stream and uses the latest partial as the "what to paste" buffer the moment the user releases the hotkey. No new whisper client — just reuse the existing webapp server as the transcription scheduler. Single source of truth for both surfaces.

**Cost.** N transcribe passes instead of 1, each over the running tape. For a 30 s take: maybe 10–15 passes of 1.5 s each = 15–22 s of GPU time vs 1.5 s today. That's the price of the latency cut. Two mitigations:

- A 2-second "quiet floor" debounce before kicking off a pass keeps mid-pause passes from happening.
- Configurable `partial_interval_seconds` (default 2.0) so a CPU-only box can dial it to 5.0 or off entirely.

**Risk: whisper "drift".** Each pass re-transcribes from scratch, so consecutive partials can disagree on earlier words (whisper might change "their" to "there" two seconds later as context grows). The UI must accept this — the transcript box gets *replaced*, not appended. Keep the last partial visible; flicker is acceptable; jitter is not, so debounce UI updates to ~250 ms.

---

### Pillar 2 — Speculative polish

**Idea.** The moment a partial transcript stabilises for ~600 ms (no new words), kick off a polish call on it. If a newer partial arrives mid-flight, *cancel* the in-flight polish and start a new one. Always keep the latest *completed* polish in `session.polished_speculative`. When the user hits Stop (or VAD auto-stops), if the final transcript matches the one we last polished, we just serve the cached result. Done. Zero LLM wait on the critical path.

**Why this works in practice.** Filler-word polish is roughly idempotent: removing "um" from a 20-word transcript doesn't change much when word 21 arrives. The cancel-and-retry chain settles fast because the user pauses naturally. Worst case, the *last* polish runs after stop — but it's already ~300–800 ms in by then, so the user-visible wait is whatever's left.

**Implementation shape.**

In the polish flow on the server:

1. The rolling-transcription worker also signals "transcript settled" (no new partial in 600 ms).
2. A *polish supervisor* coroutine watches that signal. When it fires, if the latest partial differs from the last-polished text, it launches a polish call with an `asyncio.Task` it owns.
3. New partial arrives mid-flight → cancel the task, fire a fresh one on the new text.
4. Task completes → write `session.polished_speculative` + emit a `polish_partial` SSE event.

On stop:
- If `session.final_transcript == session.last_polish_input`, just emit `polish_final` from cache.
- Else run one last polish, blocking, on the actual final transcript.

**Hub-side behaviour (verified — see "Cross-repo: local-llm-hub" section below).** For the default `agentic_light` / `agentic_heavy` polish roles (llama-server upstream), llama-server itself detects client disconnect and aborts in-flight generation within a few hundred ms, freeing the hub thread cleanly. For `claude-*` / `gemini-*` polish, the hub spawns a CLI subprocess that does not cancel on our disconnect — those calls run to completion and we throw the result away. Bounded waste, never a correctness issue. **No hub changes are required to ship Pillar 2.** A clean hub-side enhancement is sketched in "Cross-repo" below for hand-off if we later want true cancellation on the cloud paths.

**Cost.** N polish calls instead of 1 per take. For typical 15–30 s dictations the chain converges in 2–4 calls. On `agentic_light` (4B local), that's ~3–8 s of hub CPU per take. Acceptable while the hub is otherwise idle; configurable cap (`max_speculative_polishes_per_session`, default 6) prevents runaway on long takes.

**The interesting design choice: which polish styles get speculative treatment.**

- `filler-words` — yes, it's near-idempotent and cheap.
- `grammar-only` — yes, same reasoning.
- The (planned) `enrich` agentic style — **no**. Tool-using agents are expensive, non-idempotent, and "the user is still talking" is exactly the wrong signal to fire one. Flag speculative behaviour per-entry in `config/polish_prompts.json` (`"speculative": true|false`, default true for non-agentic, false for agentic).

---

### Pillar 3 — VAD-driven auto-stop

**Idea.** Whisper.cpp ships `whisper-vad-speech-segments.exe` and the Silero VAD model. We piggy-back: while recording, run VAD on the streaming audio in a sidecar process. When VAD has reported "no speech" for ≥ `auto_stop_silence_ms` (default 1500 ms), the server triggers stop on its own.

**Why VAD and not the existing RMS gate.** `src/silence.py` is a whole-clip RMS-vs-dBFS check that runs *after* recording. We don't want that — we want a real-time speech/no-speech classifier so we can stop *during* recording. Silero is small (a few MB), runs in CPU in real time, and is built for exactly this.

**Implementation shape.**

- On the webapp/desktop side, the VAD check can also run in the browser (Silero ONNX has a JS build, ~2 MB) — emit "silence reached" to the page, the page calls `/finish`. Keeps the server stateless about VAD timing.
- On the tray hotkey path (no browser), the recorder thread runs a tiny ONNX Silero through `onnxruntime` (already not in `requirements.txt` — would add it). 80 KB of state per session. Cheap.

**UI.** A `🤖 Auto-stop on silence` toggle in the webapp header, the tk window, and the tray menu. Default off for the first release; on by default once we trust it for two weeks. Per-surface ephemeral toggle, matching the existing pattern for Translate / Incognito / Append.

**Risk: false-positive auto-stops.** Thinking pause vs done-talking is ambiguous; cutting the user off mid-thought is *much worse* than them tapping Stop themselves. Two defences:

- Default `auto_stop_silence_ms` to 1500 ms — long enough to cover natural pauses, short enough to feel responsive.
- A 500 ms "grace" period after auto-stop fires: the page shows "Auto-stopping in 0.5 s — keep talking to cancel". Any new chunk in that window aborts the stop. Trivial to wire, makes the feature feel safe.

---

## How the three pillars compose

Each pillar is independently useful. The shape of the wins:

| Combination | Stop-to-clipboard latency (30 s take, tower) | What's still visible to the user |
|---|---|---|
| Today (baseline) | ~4–6 s | "Server: whisper…" then "polishing…" |
| Pillar 1 only | ~1.5–2.5 s | Polish wait only — transcription is done |
| Pillars 1 + 2 | ~300–800 ms | Most takes feel instant; long takes still see one short polish call |
| Pillars 1 + 2 + 3 | Same as above, but the user doesn't tap Stop | Hands-free flow |

The order to ship them is the order above — 1 before 2 (2 depends on 1's partial pipe), 3 last (it's a UX layer over the same pipe, not on the critical path of the latency cut).

---

## Phasing — what to build, in what order

**Phase A — Rolling transcription on the webapp** (one weekend)

- Server: background worker per active session, ticker, `partial.txt` write, SSE endpoint.
- Client: SSE consumer in `app.js`, live transcript rendering, replace-not-append.
- Config: `partial_interval_seconds` in `config/webapp_config.json`, default 2.0; set to 0 to disable.
- Tests: a manual checklist in `docs/2026-MM-DD-rolling-transcription.md`; verify a 30 s take produces ≥ 3 partials before stop, no duplicate finalisation.
- Acceptance: dictate 30 s, see the transcript grow live, hit stop, see the transcript settle within 200 ms.

**Phase B — Speculative polish** (one weekend)

- Server: polish supervisor coroutine; cancel-and-retry logic; `polish_partial` SSE event; cache check on `/finish`.
- Hub: confirm/fix client-disconnect cancellation in `claude-local-calls`. If not feasible in one session, ship with the "waste-and-discard" fallback.
- Client: render polish updates the same way as transcript partials.
- Config: `max_speculative_polishes_per_session` (default 6), per-prompt `"speculative"` flag in `polish_prompts.json`.
- Acceptance: dictate 30 s, by the time stop is tapped, the polished box is already filled, no spinner shown.

**Phase C — Desktop parity (tk window + tray hotkey)** (one weekend)

- New mode in `src/recorder.py`: `record_streaming(chunk_seconds=1.0, on_chunk=callback)` — same `sounddevice` capture, callback invoked per chunk.
- Tk window + tray switch to streaming mode and POST chunks to the local webapp's `/chunk` endpoint via loopback (skip token, already exempt).
- Tray hotkey path subscribes to SSE on loopback, copies-and-pastes the latest `polish_partial` (or `partial` if polish is disabled) the moment the user releases the hotkey.
- Memory parity: this preserves the rule that whatever the webapp does, the desktop also does.
- Acceptance: F8-driven hotkey take feels indistinguishable from the webapp flow; clipboard contains polished text within 500 ms of key release on a 15 s dictation.

**Phase D — VAD auto-stop** (half a weekend)

- Add `onnxruntime` + `silero-vad` model to `requirements.txt` and `setup.bat`.
- Server: optional sidecar VAD scorer per session, ticking on the same raw file.
- Client: 500 ms grace banner, abortable.
- Toggle on all three surfaces.
- Acceptance: with the toggle on, dictating "test one … (silence) … test two" yields two separate sessions of "test one" and "test two".

**Phase E — Durability hardening** (one weekend, lands alongside A/B/C as we go)

Durability isn't a separate feature you ship at the end — it's an invariant each preceding phase must already satisfy. Track it as its own phase only because it has its own checklist and its own acceptance test, but the *work* is spread across A/B/C:

- IndexedDB chunk queue + retry, `chunk_seq` deduplication on server (lands with Phase A so streaming partials are robust from day one).
- Atomic `partial.txt` writes + post-restart re-queue (lands with Phase A).
- Idempotent `/finish` (lands with Phase A).
- Desktop `streaming_recorder.py` with on-disk `raw.pcm` (lands with Phase C).
- `launcher.py recover` CLI + tray "🔧 Recover unsaved takes…" menu (lands with Phase C).
- SSE auto-reconnect with `Last-Event-ID` (lands with Phase A; refined in B).
- Acceptance test: scripted chaos — `Stop-Process` uvicorn mid-take, `netsh interface set interface "Wi-Fi" admin=disabled` for 30 s mid-take, kill the browser tab mid-take, kill the tray mid-take. Each scenario produces a recoverable take with no audio loss past the last fsync (≤ 5 s of data at worst).

**Total estimate:** ~3 weekends to A+B+C with durability baked in, +0.5 for D (VAD auto-stop). Two of the four spin-off plans you've already drafted are 4–6 weekends; this one is comparable in effort but flows into every existing surface immediately *and* fixes the existing "desktop has no crash recovery" gap as a side effect.

---

## What we are explicitly NOT doing

These are the temptations to resist:

- **A websocket protocol.** SSE is enough. Polling `/partial` is also enough. Don't introduce a duplex channel for a one-way data flow.
- **Streaming the polish LLM token-by-token to the page.** It looks cool but the speculative-polish model means the *last* polish is what matters, not the first one's tokens. Wait for the response.
- **Replacing the existing single-shot upload path.** Keep `/api/sessions/{id}/upload` working as a fallback. Older browsers, debug paths, and the silence-skip flow all use it.
- **A new chunked format.** Keep webm/opus as the wire format; let ffmpeg keep transcoding to WAV on demand. We're not rewriting the audio stack.
- **Tinkering with `large-v3-turbo`.** Same model, same configuration. This plan is about scheduling, not accuracy.
- **A new "low-latency" model swap.** Distilled or tiny models exist; using them is a different decision with a different tradeoff (accuracy vs speed). Latency-collapse on the current model first; if there's still a gap, that's a future plan.

---

## Cross-repo: local-llm-hub

Verified against `E:\automation\local-llm-hub\src\server.py` while drafting this plan. The hub is the **only** cross-repo dependency in this plan, and the goal stated upfront is to avoid changes there. Here is exactly what the hub does today and what (if anything) we need from it.

### What the hub already supports — no changes needed

| Capability we need | Status today | Where in the hub |
|---|---|---|
| `POST /v1/messages` for polish | ✅ working, our existing path | `src/server.py:351` |
| Multiple concurrent in-flight polish calls | ✅ — FastAPI handles fan-out; subprocess paths spawn per call; llama path is HTTP per call | — |
| Client-disconnect cancellation, **local llama backends** (`agentic_light`, `agentic_heavy`) | ✅ effectively works — llama-server upstream detects TCP close mid-generation and aborts within a few hundred ms; the hub's blocking `requests.post` returns, thread is freed | `src/openai_upstream.py` (sync `requests`) + upstream llama-server's own disconnect handling |
| Client-disconnect cancellation, **cloud backends** (`claude-*`, `gemini-*`) | ❌ subprocess keeps running until natural completion; result is discarded | `src/claude_cli.py`, `src/gemini_cli.py` — `subprocess.run` blocks, no signal forwarding |
| Streaming the polish response token-by-token | ❌ on `/v1/messages` (returns single JSON even with `stream=true`); ✅ on `/v1/chat/completions` for openai backend only | `src/server.py:353-354` warns and falls back |
| Health / model listing for status panel | ✅ `/health`, `/v1/models` | `src/server.py:332,337` |

### What this plan does **not** ask the hub to do

- We do **not** ask the hub to stream polish output. The speculative-polish design discards intermediate polish calls and only uses the latest complete one, so token-streaming has no value here.
- We do **not** ask the hub to add cancellation on the cloud paths. Cancelled `claude-*` / `gemini-*` polishes run to completion; we drop the result. The waste is bounded by `max_speculative_polishes_per_session` (default 6) and only matters at all if the user picks a `claude-*` model as the default polish — `agentic_light` (the default) is unaffected.
- We do **not** ask the hub to track per-session state, multiplex SSE, or carry any of the streaming-transcription work. That all lives in this repo.

### What we'd ask the hub for in a **future** hand-off (optional, separate plan)

If/when we decide we want true cancellation on the cloud paths, the hand-off to a hub-side agent is the following self-contained patch:

> **Hub task: propagate FastAPI client-disconnect to backend workers.**
>
> Files to touch: `src/server.py`, `src/claude_cli.py`, `src/gemini_cli.py`.
>
> 1. In `messages()` (line 351), switch from sync function to `async def`, take `request: Request` parameter, and pass `request` (or a `disconnect_event`) into the backend dispatcher.
> 2. In `_run_claude_backend` / `_run_gemini_backend`, run `call_claude` / `call_gemini` in a thread (`asyncio.to_thread`) and race it with `await request.is_disconnected()` polling at ~250 ms. On disconnect, SIGTERM the subprocess; on Windows, `subprocess.Popen.terminate()`.
> 3. In `call_claude` / `call_gemini` (currently `subprocess.run`), refactor to `subprocess.Popen` so the supervisor can hold the handle and terminate from outside. Add a 2-second SIGTERM → SIGKILL escalation.
> 4. Smoke test: open a polish request, kill the client mid-flight, confirm the `claude -p` subprocess is gone within 3 s (Process Explorer / `Get-Process claude`).
>
> Out of scope for that patch: streaming `/v1/messages`, observability traces (those belong to the eval/observability plan).

That's the entire cross-repo surface. **Until that hand-off ships, this plan is entirely additive on the hub side** — no behavioural changes, no API changes, no version bump.

### Hub-side risks to track (none blocking)

- The default `agentic_light` model runs on `127.0.0.1:8088` (qwen3.5-4b via llama-server). Llama-server serializes generation through a single slot — speculative polish calls **queue** rather than run in parallel. That's actually fine for our design: the latest call enters the slot, the user disconnects from the older ones, llama-server aborts them as their TCP sockets close.
- If the user has the `agentic_heavy` slot running on `:8087` and `agentic_light` on `:8088` (the documented dual setup), there's no cross-slot contention. Polish defaults to `agentic_light` and stays out of `agentic_heavy`'s way.
- If the local llama-server is **not** running (a state already surfaced as the famous `502 hub returned... upstream :8088 unreachable` error in the README troubleshooting table), the speculative polishes fail-fast and noisily on every chunk boundary. Defuse: pause the speculative loop on the first hub error and only retry on the final post-stop polish.

---

## Durability — surviving crashes and dropped connections

> Stated explicit requirement: a 5-minute take must never be lost. If the network drops, the laptop sleeps, the tunnel hiccups, the browser tab crashes — the audio is recoverable.

The good news: the existing webapp pipeline already persists chunks to disk on the server (`/api/sessions/{id}/chunk` appends straight into `archive/YYYY/MM/DD/HH-MM-SS-<id>/raw.webm`), and the **🔁 Redo** button in History can re-run whisper on saved audio. So the foundation is there. What's missing is the path-coverage: the *desktop* hotkey path holds 5-minute audio buffers in RAM with **zero** disk persistence, the *client* side of the webapp has no retry queue, and the rolling-transcription state introduced in Pillar 1 isn't crash-safe yet either.

This section is cross-cutting — it applies to all three pillars. Treat it as a non-negotiable acceptance criterion for each phase: a take must be **fully recoverable from disk** at every point past the first second of recording.

### Failure modes we must survive

| Failure | Where today's pipeline loses data | Where the new pipeline must not |
|---|---|---|
| User's phone loses Wi-Fi mid-record | Browser keeps recording; chunk uploads fail silently; on resume, gap in `raw.webm` | Failed chunks queue in IndexedDB with `chunk_seq`, retry-with-backoff on reconnect, server reassembles in order |
| Cloudflare tunnel disconnects briefly | Same as above + SSE stream drops | SSE auto-reconnects with `Last-Event-ID`; client backfills missed `partial`/`polish_partial` events on resume |
| Browser tab is killed (iOS aggressive backgrounding, OOM) | In-flight chunk lost; webm header buffered in `MediaRecorder` may not have been flushed | Force `mediaRecorder.requestData()` every 1 s (already done) **and** mirror every chunk into IndexedDB before the upload promise resolves — re-open page recovers from local IDB |
| PC reboots / power loss during record | Server-side `raw.webm` keeps everything received up to the last fsync; tk window / tray hotkey: **everything lost** | Desktop streaming recorder writes int16 chunks to a per-session `raw.pcm` on disk every 500 ms; orphan-recovery sweep on next boot |
| FastAPI process crashes mid-take | `raw.webm` survives; rolling partial state is gone | `partial.txt` + `partial_version` written to disk on each pass; restart re-derives partial from `raw.webm` if missing |
| Hub crashes mid-polish | Polish call 502s; transcript already on disk | Speculative loop pauses on first error, retries only the final post-stop polish (already covered above) |
| User closes the laptop / locks the phone | Browser may pause MediaRecorder | On `visibilitychange = hidden`, immediately flush pending chunks and POST `/api/sessions/{id}/keepalive` — server marks session as "suspended", retains state past the auto-cleanup window |

### Architectural additions

These are the concrete crash-safety additions, listed by surface:

**Webapp client (`app/webapp/static/app.js`)**

- Open a session-scoped IndexedDB store (`vt_chunks`) keyed by `(session_id, chunk_seq)`. Every MediaRecorder chunk lands in IDB *before* it's POSTed.
- Each `/chunk` POST carries `?seq=<int>` so the server can deduplicate and reorder. Server holds a small reorder buffer (last ~16 chunks) per session and writes to `raw.webm` in order; out-of-order arrivals wait.
- On success (HTTP 200 with `{ack_seq: N}`), the client deletes IDB entries `≤ N`. On failure, retry with exponential backoff capped at 8 s; the queue never drains a chunk it hasn't seen acked.
- On page boot, scan IDB for sessions with un-acked chunks → offer "Resume in-progress recording from <time>" banner. Tap → resume the upload chain against the existing session id; the recording itself is over (we can't keep capturing across reload) but the audio is intact server-side.
- A `📥 Download local copy` button is always present after stop: if for any reason the server-side reassembly failed (gap detected via missing `chunk_seq`), the user can drag the locally-cached webm out of IDB to the desktop and re-upload manually.

**FastAPI server (`app/webapp/server.py`)**

- `/chunk` accepts `?seq=<int>`. Server maintains an in-memory `expected_seq` per session; out-of-order chunks parked in a bounded dict until their predecessor arrives, then drained in order. Bound = 16 chunks ≈ 16 s; beyond that, log a gap and fail the session (rare).
- On every chunk write, `os.fsync()` after `f.write()` only on a 5-second cadence (not every chunk — too much I/O). The webm format is forgiving of truncation: even an un-fsynced trailing chunk replays through ffmpeg cleanly.
- New session field `meta.suspended_at` set when the client posts `/keepalive`; the boot-time cleanup sweep treats suspended sessions younger than 1 hour as live (don't prune them by retention rules).
- Rolling-transcription worker writes `partial.txt` + `partial_version.txt` atomically (write to `.tmp`, `os.replace`). On webapp restart, scan `archive/` for sessions with `raw.webm` but no `transcript.txt` AND no `partial.txt` newer than `raw.webm`'s mtime → re-queue them for transcription.
- `/finish` is **idempotent**: receiving it twice with the same `session_id` doesn't re-transcribe if `transcript.txt` already exists and `raw_bytes` hasn't grown. The client retries `/finish` with the same payload until it gets a 200; the server short-circuits the duplicate.

**Desktop side — tk window and tray hotkey (the biggest gap today)**

- New module `src/streaming_recorder.py`: thin wrapper around the existing `AudioRecorder` that, in addition to keeping samples in RAM, also appends raw int16 chunks to a per-session `raw.pcm` file every 500 ms. File is opened in the session folder created via `SessionArchive.new_session()`, same convention as the webapp.
- The tk window and tray hotkey switch to this recorder. On stop, the final WAV is reconstructed from `raw.pcm` (or, post Phase C, posted to the webapp's `/chunk` endpoint as the recording happens — single source of truth).
- New CLI: `python launcher.py recover` — lists orphan sessions (folders with `raw.pcm` or `raw.webm` but no `transcript.txt`), prompts to re-transcribe each. Same logic the boot sweep runs but interactive.
- Tray menu adds **🔧 Recover unsaved takes…** when the boot sweep finds any. One-click triggers the recover flow.

**Hotkey reliability specifically**

The hotkey path is the most "fire and forget" of the three surfaces and has the highest stakes for crash safety because the user often releases F8 expecting the text to appear *at the caret* with no further interaction. Three additions:

- Recording state file (`archive/.in_progress/<session_id>.json`) written when the hotkey starts recording, deleted on successful paste. Boot sweep on tray launch surfaces any leftover state file with a "Last take crashed mid-record — recover?" toast.
- Even if rolling transcription is on, the hotkey path *also* runs a final whisper pass post-stop unconditionally (the speculative-polish cache check still applies, so this is fast, but it guarantees the paste is on the *complete* take, not the second-to-last partial).
- If paste-at-caret fails (focused app stole focus, hotkey window context lost), the transcript stays on the clipboard *and* an `archive/last_failed_paste.txt` is written. Boot sweep restores from there too.

### What "5-minute take survives anything" looks like end-to-end

Concrete walkthrough — a 5-minute hotel-Wi-Fi dictation from the phone, network drops twice mid-take:

1. Phone records, MediaRecorder fires every 1 s → IDB write → POST `/chunk?seq=N`. Server appends to `raw.webm`, fsyncs every 5 s.
2. Tunnel drops at 1:40. IDB queue fills. Local recording continues; on each retry attempt, exponential backoff.
3. Tunnel returns at 2:05. Queue drains in order; server reassembles. Rolling transcription catches up from where `raw.webm` is now.
4. SSE auto-reconnects with `Last-Event-ID = partial-37`; server replays partial-38 onward.
5. Tunnel drops again at 3:10, comes back at 3:12. Same recovery, no user-visible disruption beyond a brief spinner.
6. User taps Stop at 5:00. Client drains last chunks, posts `/finish`. Server returns 200 with the final transcript and the cached speculative polish.
7. Worst case: at step 6 the network is still down. Client retries `/finish` every few seconds; meanwhile the page already shows the latest partial, and the **📥 Download local copy** button is enabled so the user can drag the webm to the desktop if they need it *right now*.
8. Hypothetical: phone dies between step 6 and `/finish` succeeding. On next visit (from any device), History shows the session — full audio is on the server, transcript is the final-but-uncached partial, **🔁 Redo** is one tap away.

There is **no point in the 5-minute take** where data is held only in volatile memory. That is the invariant.

---

## Risks and how I'd defuse each

| Risk | How to defuse |
|---|---|
| Whisper drift between partials confuses the user | Replace transcript wholesale on each update; debounce UI to 250 ms; never show two partials side-by-side |
| Speculative polish flickers the polished box | Only commit a new polish to the visible box if it differs from the current one by > N chars (avoid 1-char churn); render with `aria-live="polite"` |
| GPU starvation from N transcribe passes per take | `partial_interval_seconds` config; dirty-flag debounce so passes never overlap; one in-flight call per session |
| Hub doesn't cancel polish requests cleanly | Fallback path: let the doomed polish run, throw away the result. Wastes hub CPU only, never affects correctness |
| Auto-stop cuts the user off mid-sentence | 500 ms grace period banner with "keep talking to cancel"; default off; per-surface ephemeral toggle |
| Two recordings overlap on the local llama-server while speculatively polishing | Hub already serializes; this is the hub's problem, not ours. Surface 503/424 in the SSE stream, retry once on the final pass |
| Mobile Safari SSE quirks (idle drop on backgrounded tab) | Auto-reconnect with `Last-Event-ID`; tolerate gaps; final-stop pass is the safety net |

---

## Open questions to confirm before phase A starts

1. **Partial interval default.** I've proposed 2.0 s. Want it tighter (1.0 s — smoother visual, ~2× GPU cost) or looser (3.0 s — calmer feel, slightly more "is it working?")?
2. **SSE vs polling.** I'd default SSE — simpler client code, no jitter, and Cloudflare passes it cleanly. Any reason to prefer polling (e.g. easier debug, simpler proxy)?
3. **Phase C ordering.** Build desktop parity (Phase C) immediately after A+B, or punt to a follow-up so A+B can settle on the webapp first? The feature-parity rule says ship all three together; the engineering pragma says webapp-first lets us iterate on the partial-flicker UX before locking the desktop in.
4. **VAD default.** Off on first release, opt-in toggle? Or on-by-default once we're confident?
5. **Speculative polish on the hotkey path.** It's the most aggressive use of the feature — the user releases F8 and expects *polished* text at the caret, not raw. Worth a separate `paste_polished_when_available` toggle, or always-on once polished is ready?
6. **Hub cancellation — defer or hand off now?** I'd recommend defer: speculative polish works fine on the hub as-is for the default `agentic_light` role, and the cloud-route waste is bounded and only matters if the user picks a `claude-*` polish default. The hub-side patch in "Cross-repo" above is ready to hand off if you want it done in parallel, but it adds zero capability to this plan's user-visible behaviour.
7. **Durability acceptance bar.** I've proposed "≤ 5 s of audio loss at worst (last un-fsynced window)". Acceptable, or do you want stricter (every-chunk fsync, ~50 ms slower per chunk write but lossless modulo physical disk failure)?

---

## Why this is the right "2x effort, 10x outcome" bet

- **It hits every surface** (webapp, tk, tray hotkey, future iOS), so the parity rule isn't a tax — it's a multiplier.
- **It's invisible until shipped, then unmissable.** Latency improvements have a step-function feel; users don't notice 20% wins, they notice the moment a tool "becomes instant".
- **Nothing in the plan invents new tech.** Whisper.cpp does VAD and streaming. The webapp already chunks. The hub already polishes. We're scheduling.
- **It composes with the four existing plans, doesn't compete.** Agentic-polish becomes the explicit slow lane; eval/observability gets multi-stage traces to score; iOS keyboard inherits the same SSE protocol; multi-tenant inherits per-session scheduling that was already isolated.

If the goal is "10× better daily-driver feel for ~2× effort", this is the plan that gets there without buying anything you can't ship.
