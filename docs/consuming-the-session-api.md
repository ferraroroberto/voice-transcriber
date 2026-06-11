# Consuming the session API

A guide for **downstream apps** that want robust, never-lose-it
recording + transcription without re-implementing the
`MediaRecorder → chunk-to-disk → whisper` plumbing. Call this app's
session API instead.

This is a **supported, consumable integration surface**. The
voice-transcriber is the canonical local audio service in the fleet,
the way [`claude-local-calls`](https://github.com/ferraroroberto/claude-local-calls)
is the canonical LLM hub — downstream apps proxy to it over loopback
rather than duplicating the capture/transcribe stack.

First consumer: **grocery-shopping-automation**'s Audio Audit
(companion issue `ferraroroberto/grocery-shopping-automation#30`).

> **Contract status.** The `/api/sessions*` routes documented here are
> a **stable contract**. Breaking changes are recorded in the
> [Changelog](#changelog) at the bottom of this file. Pin the build you
> tested against via `GET /api/version` (`git_sha`) if you need
> certainty across upgrades.

---

## TL;DR

Two ways to get a transcript:

1. **Single-shot** — you already have a complete audio blob. One call:
   `POST /api/sessions` → `POST /api/sessions/{id}/upload`. Simplest.
2. **Streamed (never-lose-it)** — you're recording live and want the
   audio safe on disk as it arrives, with rolling partial transcripts:
   `POST /api/sessions` → repeated `POST .../chunk` (1 s cadence) →
   optional `GET .../events` (SSE) → `POST .../finish`. If the caller
   dies mid-record, the streamed audio is already on disk and
   recoverable via `POST .../retranscribe`.

Same-host callers need no auth and `verify=False` on the loopback cert.

---

## Base URL, transport, auth

| Concern | Same-host (loopback) consumer | Remote consumer |
|---|---|---|
| Base URL | `https://127.0.0.1:8443` | your Cloudflare URL, e.g. `https://voice.<domain>` |
| TLS | self-signed loopback cert → `verify=False` | Cloudflare terminates TLS; normal verification |
| Auth | **none** — loopback IPs bypass the gate | bearer token required when enabled |

**Loopback bypass.** `app/webapp/middleware.py` lets any caller from
`127.0.0.1`, `::1`, or `localhost` through without a token — the same
mechanism the tk window uses. A downstream app running on the same PC
needs no credentials at all.

**Self-signed cert.** The webapp serves HTTPS on loopback so the local
browser keeps a secure context for `getUserMedia`. A same-host HTTP
client must skip verification (`httpx.Client(verify=False)` /
`curl -k`) — the cert is not in your client's trust store.

**Remote auth (only if the token gate is on).** When
`auth_token` is set in `config/webapp_config.json`, non-loopback callers
must present it as either:

- `Authorization: Bearer <token>` header (preferred for API clients), or
- `?token=<token>` query string (needed for `EventSource`, which can't
  set headers — see the SSE section).

Exempt paths that never need the token: `/`, `/static/*`, `/healthz`,
`/install-ca`, `/api/login`, `/api/version`.

---

## Lifecycle overview

```
POST /api/sessions                       create  → {session_id, ...}
  │
  ├── single-shot ──────────────────────────────────────────────┐
  │   POST /api/sessions/{id}/upload      whole blob → transcript │
  │                                                               │
  └── streamed ──────────────────────────────────────────────────┤
      POST /api/sessions/{id}/chunk       raw bytes, 1 s cadence  │
      GET  /api/sessions/{id}/events      SSE partial/final       │
      POST /api/sessions/{id}/finish      canonical transcript    │
                                                                  │
      POST /api/sessions/{id}/retranscribe   recover a saved take │
                                                                  │
  DELETE /api/sessions/{id}              drop (e.g. incognito)    ┘
```

Every chunk is archived to disk the moment it lands, **before**
`/finish`, so a dropped connection or dead phone is always recoverable.

---

## Endpoints

### `POST /api/sessions` — create

Request body (JSON, all optional):

```json
{ "language": "en", "incognito": false }
```

- `language` — Whisper ISO code (`en`, `es`, …) or lowercase English
  name (`english`, …). Defaults to the app's configured language.
- `incognito` — when `true`, the session never appears in the History
  list. Pair with `DELETE` when you're done (see
  [Incognito](#incognito)).

Response:

```json
{
  "session_id": "14-32-07-a1b2c3d4",
  "folder": "E:\\automation\\voice-transcriber\\archive\\2026\\06\\11\\14-32-07-a1b2c3d4",
  "created_at": "2026-06-11T14:32:07+00:00",
  "incognito": false
}
```

`session_id` is the handle for every subsequent call.

---

### `POST /api/sessions/{id}/chunk` — stream a chunk

Append one streamed audio chunk to the session's raw file on disk. The
body is the **raw chunk bytes** — no multipart wrapping. Send the
`Content-Type` header matching your recorder's MIME (`audio/webm` or
`audio/mp4` — see [MIME / format](#mime--format)).

The recommended cadence is **one chunk per second** (this is what the
browser client uses: `MediaRecorder.start(1000)`), which keeps latency
low and survives connection drops.

Response:

```json
{ "session_id": "14-32-07-a1b2c3d4", "raw_bytes": 24576 }
```

`raw_bytes` is the cumulative size on disk. An empty body is a no-op
that just echoes the current size. Each chunk also nudges the
rolling-transcription worker so the next partial pass picks up the new
bytes.

---

### `GET /api/sessions/{id}/events` — rolling transcripts (SSE)

A [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events)
stream of rolling transcription. Open it right after you start
streaming chunks; consume events until `final`. Optional — skip it if
you only want the transcript at the end.

Two event kinds:

| `event:` | `data:` payload | Meaning |
|---|---|---|
| `partial` | `{"version": 3, "transcript": "..."}` | A rolling pass over the audio so far. `version` increments each pass; later passes can revise earlier words (whisper is a sliding-context model). |
| `final`   | `{"transcript": "..."}` | The canonical transcript — fired by `/finish`. Stop reading after this. |

The stream opens with a `:ok` comment line to flush proxies, and
backfills the latest `partial` so a late subscriber sees current state
immediately.

**Auth note for `EventSource`.** Browser `EventSource` can't set
headers, so a remote consumer passes the token in the query string:
`GET /api/sessions/{id}/events?token=<token>`. Same-host consumers need
nothing.

Disable rolling transcription entirely by setting
`partial_interval_seconds: 0` in `config/webapp_config.json` — then no
`partial` events fire and you rely on `/finish` alone.

---

### `POST /api/sessions/{id}/finish` — canonical transcript

Close a streamed session: transcode the accumulated raw audio to WAV,
run whisper, persist `transcript.txt`, return the text. Broadcasts the
`final` SSE event and tears down the rolling worker.

Query params (both optional):

- `language` — override the session's language for this pass.
- `translate` — `true` routes to the translate-capable whisper instance
  (`:8091`, speak-X-get-English). Default `false`.

Optional JSON body: `{ "duration_seconds": 12.4 }` (recorded in meta).

Response:

```json
{
  "session_id": "14-32-07-a1b2c3d4",
  "transcript": "the canonical text",
  "language": "en",
  "from_partial": true
}
```

`from_partial` is present and `true` when the last rolling partial
already covered the whole take, so whisper was not re-run. On
**near-silent** audio the response is instead:

```json
{ "session_id": "...", "transcript": "", "language": "en", "silent": true, "dbfs": -57.3 }
```

(the loudness gate skips whisper so it can't hallucinate on empty
input).

Errors: `400` if no chunks were ever received, `503` if ffmpeg is
missing, `500` on transcode failure, `502` on a whisper error.

---

### `POST /api/sessions/{id}/upload` — single-shot

For when you already have a complete audio blob (no streaming).
**Multipart** form upload — field name `file`:

```
POST /api/sessions/{id}/upload?language=en&translate=false
Content-Type: multipart/form-data
  file=<the audio blob>
```

The whole blob is persisted to disk first (so it survives any later
failure), then transcoded + transcribed. Same response shape as
`/finish` (including the `silent` variant). Errors: `400` on an empty
upload, plus the same `503/500/502` transcode/whisper errors.

---

### `POST /api/sessions/{id}/retranscribe` — recover a saved take

Re-run whisper on the raw audio already on disk — the crash-recovery
path. Use it when a streamed session's caller died before `/finish`
landed: the chunks are still on disk, so this recovers the transcript
after the fact. Same query params and response shape as `/finish`.
`404` if the raw audio is missing.

---

### Reading back: text, list, delete

- `GET /api/sessions/{id}/text` → `{session_id, transcript, polished}`
  — the full text (the list endpoint only returns 200-char previews).
- `GET /api/sessions?limit=10&offset=0` → `{sessions: [...], total, offset, limit}`
  — history list with per-session metadata + previews.
- `DELETE /api/sessions/{id}` → `{removed: "<id>"}` — drop one session
  (folder and all). `404` if unknown.
- `DELETE /api/sessions` → `{removed: <count>}` — drop everything.

#### Date-window retrieval (`days` / `since`)

`GET /api/sessions` accepts an optional date window so a consumer can
pull a last-N-days slice directly instead of paging the whole history
and filtering client-side:

- `days=N` → only sessions created within the last `N` days.
- `since=<ISO 8601>` → only sessions created at or after that instant;
  overrides `days` when both are given.

The window is applied **before** `offset`/`limit`, and incognito
sessions stay excluded. A non-positive `days` or an unparseable `since`
returns `400`. Timestamps are compared in the server's local time
frame (the same frame `created_at` is written in).

```bash
# the 7-day history window, newest first
curl -sk "https://127.0.0.1:8443/api/sessions?days=7&limit=200" | jq '.total'
```

#### Bulk transcript export (`GET /api/sessions/transcripts`)

For mining/analytics over a window without the N+1 `/text` fetches, the
bulk endpoint returns every non-incognito session's **full transcript**
in one call:

```
GET /api/sessions/transcripts?days=7        → {transcripts: [...], count}
```

Each entry is `{session_id, created_at, transcript}` (newest-first);
empty/whitespace-only transcripts are omitted. Accepts the same
`days`/`since` window as the list endpoint, plus an optional `limit`.

```bash
curl -sk "https://127.0.0.1:8443/api/sessions/transcripts?days=7" \
  | jq '.count'
```

> **Blessed use-case: transcript mining.** The hub dictionary miner
> ([`ferraroroberto/local-llm-hub#94`](https://github.com/ferraroroberto/local-llm-hub/issues/94))
> consumes this window/bulk surface over loopback to infer recurring
> domain vocabulary and mis-transcriptions, then suggests glossary
> updates. The corpus stays here (provider-agnostic ownership); the hub
> depends only on this documented contract + a configured base URL. Pin
> the tested build via `GET /api/version` (`git_sha`).

### `GET /api/version` — build identity (pin point)

```json
{ "git_sha": "4400645", "built_at": "2026-06-11T...", "asset_hash": "ab12cd34" }
```

Exempt from auth. Use `git_sha` to pin the build you integration-tested
against. `GET /healthz` → `{"ok": true, "service": "voice-transcriber-webapp"}`
for a plain liveness probe.

---

## MIME / format

The `Content-Type` you send on each `/chunk` (and on `/upload`) should
match the bytes your recorder emits. The browser client's
`pickMimeType` ladder picks the first supported of:

```
audio/webm;codecs=opus   ← Chrome/Firefox/Android
audio/webm
audio/mp4;codecs=mp4a.40.2   ← iOS Safari (often the only one)
audio/mp4
```

So expect `audio/webm` from most clients and `audio/mp4` from iOS
Safari. **The server doesn't care what you label it** — on disk the raw
file is always named `raw.webm` regardless of the real container, and
ffmpeg sniffs the actual format at transcode time. Send a truthful
`Content-Type` anyway; it's recorded in `meta.json` as `raw_format`.

If you're a non-browser consumer with arbitrary audio, anything ffmpeg
can decode works for the single-shot `/upload` path.

> ffmpeg must be on `PATH` or in `vendor/ffmpeg/` for any transcode to
> succeed. The webapp logs a warning at boot if it's missing;
> `/finish` and `/upload` return `503` until it's installed
> (`winget install Gyan.FFmpeg`).

---

## Safety guarantees

- **Chunks hit disk before `/finish`.** Every `/chunk` body is appended
  to `archive/YYYY/MM/DD/HH-MM-SS-<id>/raw.webm` immediately. A dropped
  connection, killed process, or dead phone never loses the audio
  already streamed.
- **Recover with `/retranscribe`.** After a crash, call it on the same
  `session_id` to pull the transcript from the saved raw audio.
- **Silence gate.** Near-silent takes skip whisper entirely (the
  `silent: true` response), so you never get a hallucinated
  "Thanks for watching!" on an empty recording.

---

## Incognito

A consumer that doesn't want its takes sitting in History for the
30-day retention window:

1. `POST /api/sessions` with `{ "incognito": true }` — the session is
   flagged server-side and excluded from `GET /api/sessions`.
2. Do the normal record/finish/transcribe flow.
3. `DELETE /api/sessions/{id}` when done — the folder is removed from
   disk.

The session works normally during its lifetime; incognito only governs
History visibility and your obligation to clean it up.

---

## End-to-end examples

### curl — single-shot

```bash
# 1. create a session (loopback, no auth, -k for the self-signed cert)
SID=$(curl -sk -X POST https://127.0.0.1:8443/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"language":"en"}' | jq -r .session_id)

# 2. upload a complete clip and get the transcript
curl -sk -X POST "https://127.0.0.1:8443/api/sessions/$SID/upload?language=en" \
  -F file=@clip.webm | jq .transcript
```

### Python `httpx` — streamed, never-lose-it

```python
import time
import httpx

BASE = "https://127.0.0.1:8443"
# Same-host: loopback bypasses auth, verify=False for the self-signed cert.
client = httpx.Client(base_url=BASE, verify=False, timeout=120.0)

# 1. create
sid = client.post("/api/sessions", json={"language": "en"}).json()["session_id"]

# 2. stream raw chunks as they arrive (1 s cadence mirrors the browser)
for chunk in produce_audio_chunks():           # your source of bytes
    client.post(
        f"/api/sessions/{sid}/chunk",
        content=chunk,                          # raw body, NOT multipart
        headers={"Content-Type": "audio/webm"},
    )
    time.sleep(1.0)

# 3. finish → canonical transcript (chunks are already safe on disk)
result = client.post(f"/api/sessions/{sid}/finish", params={"language": "en"})
print(result.json()["transcript"])

# If this process had died before /finish, recover later with:
#   client.post(f"/api/sessions/{sid}/retranscribe", params={"language": "en"})
```

### Python — consuming the SSE partial stream

```python
import json
import httpx

with httpx.Client(base_url=BASE, verify=False, timeout=None) as c:
    with c.stream("GET", f"/api/sessions/{sid}/events") as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
                if event == "partial":
                    print("partial", payload["version"], payload["transcript"])
                elif event == "final":
                    print("FINAL", payload["transcript"])
                    break
# Remote consumers append ?token=<token> to the URL — EventSource and
# header-less stream clients can't set Authorization.
```

---

## Error reference

| Status | When | Notes |
|---|---|---|
| `400` | empty `/upload`, or `/finish` with no chunks; unknown polish model | bad request shape |
| `401` | remote caller, token gate on, token missing/wrong | loopback never sees this |
| `404` | unknown `session_id`; `/retranscribe` with no raw audio | |
| `424` | polish failed (LLM hub unreachable) | 424 not 502 so the JSON body survives the Cloudflare tunnel |
| `500` | transcode (ffmpeg) failure | |
| `502` | whisper transcription error | |
| `503` | ffmpeg not installed | install ffmpeg, retry |

---

## Changelog

Breaking changes to the `/api/sessions*` contract are recorded here.
Pin a build via `GET /api/version` (`git_sha`) if you need certainty.

- **2026-06-11** — `GET /api/sessions` gains optional `days`/`since`
  date-window params; new `GET /api/sessions/transcripts` bulk
  full-transcript export over the same window. Additive — existing
  callers are unaffected. Blesses transcript mining as a supported
  downstream use-case (issue #60, for hub miner #94).
- **2026-06-11** — Initial publication of the session API as a supported
  consumable surface (issue #57). No behavioural change; the routes
  already worked over loopback.
