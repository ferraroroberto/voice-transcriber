# 🎙️ Voice Transcription

Local, fast, always-on voice-to-text powered by a bundled
[`whisper.cpp`](https://github.com/ggerganov/whisper.cpp) server.
Designed as a drop-in WhisperFlow replacement: tray-resident app, global
hotkey, instant record → transcribe → clipboard. CUDA on by default (via
the cuBLAS prebuilt), falls back to CPU cleanly.

Self-contained: clone the repo, run `setup.bat`, and the whisper.cpp
binary + `ggml-large-v3-turbo.bin` model are downloaded into `vendor/`
and used from there — no shared system install required.

## 🚀 Quickstart

```bat
git clone <this-repo>
cd transcribe_voice
setup.bat                 :: creates .venv, installs deps, fetches whisper.cpp + model
tray.bat                  :: system-tray mode, global hotkey F8 (tap = toggle, hold = push-to-talk)
```

That's it. The tray launches and, if no server is running yet, spawns the
bundled `whisper-server` with the configured model. On exit it stops the
server it started.

## 📋 Modes of use

| Command                  | What it does                                                              |
|--------------------------|---------------------------------------------------------------------------|
| `tray.bat`               | Resident tray icon + global hotkey (default: `F8` — **tap to toggle**, **hold ≥ 300 ms for push-to-talk**). Result auto-pastes at the caret in the focused window. Day-to-day default. Also boots whisper-server, the webapp on `:8443`, and the Cloudflare tunnel (when `webapp/cloudflared.yml` exists). |
| `webapp.bat`             | Standalone FastAPI webapp on `https://127.0.0.1:8443` — for headless / dev use. The tray spawns this for you in normal use. |
| `webapp_tunnel_named.bat`| Webapp + Cloudflare named tunnel without the tray — for headless boxes. Tray.bat already covers this in normal use. See "Persistent URL via Cloudflare tunnel" below for the one-time setup. |
| `server.bat start|stop|status|logs` | Direct control of the whisper-server process.             |
| `setup.bat`              | One-shot installer (venv + deps + whisper.cpp + ggml model).             |

Or use the Python entry point directly:

```bash
python launcher.py                  # defaults to tray
python launcher.py tray
python launcher.py record [--language MODE] [--start-server]
python launcher.py transcribe <file> [--language MODE]
python launcher.py gui
python launcher.py server start|stop|status|logs
```

All subcommands accept `--debug` and `--config path/to/config.json`.

## 🔧 Configuration

Two config files live under the repo: one for the app, one for the server.

### `config/config.json` — app-level

```json
{
  "language": "english",
  "max_record_seconds": 300,
  "sample_rate": 16000,
  "preferred_mics": null,
  "machine_specific_mics": {
    "laptop": ["Micrófono (Realtek(R) Audio)"],
    "tower":  ["el gato wave XLR (Elgato Wave XLR)"]
  },
  "hotkey": "<F8>",
  "auto_copy": true,
  "auto_start_server": false,
  "log_level": "INFO",
  "auto_paste_after_hotkey": true,
  "ptt_threshold_ms": 300,
  "translate_base_url": "http://127.0.0.1:8091"
}
```

`language` accepts any of the 100 Whisper-supported languages, either as a
Whisper ISO code (`en`, `es`, `haw`, `yue`) or as the lowercase English
name (`english`, `spanish`, `italian`, …). Common values:

| value                | dictate in | clipboard output |
|----------------------|------------|------------------|
| `en` *(default)*     | English    | English          |
| `es`                 | Spanish    | Spanish          |
| `it`                 | Italian    | Italian          |

Other knobs:

- `auto_paste_after_hotkey` — when `true` (default), the tray simulates
  `Ctrl+V` into the focused window after a hotkey-driven transcription so
  the text lands at the caret instead of just on the clipboard. Tray menu
  has a 📌 Paste at caret toggle. Hotkey-flow only — tk window and webapp
  records are unaffected.
- `ptt_threshold_ms` — F10 is a single key with two modes: tap to toggle
  (start/stop), or hold ≥ this many ms and release for push-to-talk. The
  default 300 ms is comfortable; raise it if a tap occasionally registers
  as PTT.
- `translate_base_url` — the secondary whisper-server URL used when the
  🌐 Translate toggle is on. Defaults to the local-llm-hub's `:8091`
  contract. See "Translation" below.

Two optional companion files (both gitignored, sample-tracked):

- `config/vocabulary.json` — per-language buckets of proper nouns, brands,
  and jargon Whisper would otherwise mishear. Joined into the request's
  `prompt` so the decoder biases toward those words. See
  `config/vocabulary.sample.json` for the schema.
- `config/snippets.json` — short keys auto-expanded in the transcript
  before it hits clipboard / caret paste (e.g. `"myemail"` →
  `"you@domain.com"`). Word-boundary, case-insensitive matching. See
  `config/snippets.sample.json`.

Both files hot-reload on mtime change — no restart needed after editing.

Hotkey uses [`pynput.keyboard.GlobalHotKeys`](https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys)
syntax: angle-bracketed modifiers + a key, `+`-separated.

### `whisper_server/whisper_server.yaml` — server-level

The key knob is **`mode`**:

| `mode`     | Behaviour on startup                                                             |
|------------|----------------------------------------------------------------------------------|
| `local`    | **This project owns the server.** If `host:port` is already in use by something else, refuse to start and surface a clear error. Otherwise spawn the bundled `whisper-server`. Stop it on exit. |
| `external` | **Reuse an already-running server if present** (e.g. one started by the sibling [`claude-local-calls`](https://github.com/ferraroroberto/claude-local-calls) hub). If nothing is listening, spawn our own binary. Never stops a server it didn't start. |

Switch by editing the top of `whisper_server/whisper_server.yaml`:

```yaml
mode: external   # or: local

server:
  host: "127.0.0.1"
  bind_host: "0.0.0.0"
  port: 8090           # fixed — collides on purpose if a second instance tries

binary:
  path: "vendor/whisper.cpp/whisper-server"

model:
  path: "vendor/whisper.cpp/models/ggml-large-v3-turbo.bin"

args:
  - "--threads"
  - "4"
  - "--processors"
  - "1"
  - "--inference-path"
  - "/v1/audio/transcriptions"
```

Change `model.path` to point at any other ggml model you've downloaded,
e.g. `models/ggml-small.bin` after `python scripts/download_model.py
--model ggml-small.bin`.

## 🔍 Which model is serving?

The tray, main window, and CLI all surface the active model so you can
confirm it without grepping logs.

- **Tray menu**: the first entry shows the configured model
  (`🧠 large-v3-turbo`). Pick **ℹ Model info…** for a full dump.
- **Main window**: a `🧠 …` line appears under *Server* with the model
  name, on-disk size, and resident memory. **ℹ Details** opens a dialog
  with mode, path, binary, endpoint, threads/processors, and the
  whisper.cpp startup diagnostics (backend CPU/CUDA, model type,
  languages, `system_info`) parsed from the captured log.
- **CLI**: `server status` prints the same block:

  ```
  ✅ running (started by this process) @ http://127.0.0.1:8090 pid=1234 [ours]
  📐 mode      : local
  🧠 model     : large-v3-turbo
     path      : vendor/whisper.cpp/models/ggml-large-v3-turbo.bin
     size      : 1.6 GB
  🛠️  binary    : vendor/whisper.cpp/whisper-server.exe
  🌐 endpoint  : http://127.0.0.1:8090/v1/audio/transcriptions
  ⚙️  runtime   : threads=4, processors=1
  💾 memory    : 1.7 GB (pid 1234)
     backend   : using CUDA backend
     model type: 5 (large)
     languages : 99
     system    : n_threads = 4 / 16 | AVX = 1 | ...
  ```

## 🗂️ Layout

The repo follows the monorepo's `src/` (logic) + `app/` (UI surfaces) split — same convention used by `local-llm-hub`, `grocery-shopping-automation`, and `facilitation-shuffle`.

```
voice-transcriber/
├── launcher.py                    # entry point (python launcher.py tray|record|gui|…)
├── setup.bat                      # one-shot: venv + pip + whisper.cpp + model
├── tray.bat                       # default daily launcher (boots whisper + webapp + Cloudflare tunnel)
├── webapp.bat                     # standalone FastAPI webapp launcher (no tunnel)
├── webapp_tunnel_named.bat        # webapp + named Cloudflare tunnel, no tray (headless use)
├── server.bat                     # raw server start|stop|status|logs
├── requirements.txt
├── .gitignore
├── src/                           # ── LOGIC layer (no UI imports) ──
│   ├── app_config.py              # AppConfig loader
│   ├── recorder.py                # sounddevice capture
│   ├── transcription_client.py    # HTTP → whisper server
│   ├── diagnostics.py             # log ring + port-owner introspection
│   ├── polish.py                  # local-llm-hub client (system-prompt-driven polish)
│   ├── polish_prompts.py          # loader for the polish-style library
│   ├── archive.py                 # dated session folders + 30-day cleanup
│   ├── webapp_config.py           # typed loader for config/webapp_config.json
│   └── whisper_server/
│       ├── manager.py             # spawn / kill / health / PID / describe
│       └── whisper_server.yaml    # mode, paths, port, CLI args
├── app/                           # ── UI surfaces ──
│   ├── gui/
│   │   ├── app.py                 # tkinter main window (with polish row)
│   │   ├── tray.py                # pystray + pynput hotkey + webapp lifecycle
│   │   ├── recording_popup.py     # compact VU popup
│   │   └── diagnostics_window.py
│   ├── cli/
│   │   ├── main.py                # argparse dispatcher
│   │   └── commands/              # record, transcribe, gui, tray, server
│   └── webapp/                    # FastAPI mobile-first web UI
│       ├── server.py              # routes + lifespan (cleanup on boot)
│       ├── manager.py             # adopt-or-spawn for uvicorn (used by tray)
│       └── static/
│           ├── index.html         # single-page UI, big-button mobile-first
│           ├── app.js             # MediaRecorder + chunked upload + clipboard
│           └── styles.css         # touch targets ≥ 56 px
├── config/
│   ├── config.json                # app config (language, hotkey, mics, webapp section)
│   ├── polish_prompts.json        # committed — polish-style library (system prompts)
│   ├── webapp_config.json         # gitignored — polish model/style, retention, mic prefs
│   └── webapp_config.sample.json  # committed schema example
├── docs/
│   ├── 2026-05-07-mobile-webapp-and-repo-cleanup.md   # design doc
│   └── 2026-05-08-multi-prompt-polish-and-webapp-ui.md
├── scripts/
│   ├── install_whisper_cpp.py     # download prebuilt cuBLAS whisper.cpp
│   ├── download_model.py          # fetch ggml model from HF
│   └── gen_ssl_cert.py            # self-signed CA + iOS .mobileconfig
├── archive/                       # gitignored runtime data — sessions
└── vendor/                        # gitignored, populated by setup.bat
    └── whisper.cpp/
        ├── whisper-server(.exe)
        ├── *.dll                  # CUDA runtime (Windows, cublas build)
        └── models/
            └── ggml-large-v3-turbo.bin
```

## 🧪 First run checklist

```bat
REM 1. Binaries + model present?
dir vendor\whisper.cpp\whisper-server.exe
dir vendor\whisper.cpp\models\ggml-large-v3-turbo.bin

REM 2. Server comes up?
server.bat start
server.bat status

REM 3. Quick record test
quick_record.bat
```

If the server doesn't come up, `server.bat logs` prints what
`whisper-server` printed on startup (backend, model-load lines, system
info). A CUDA-backed build will log `whisper_backend_init: using CUDA
backend`; a CPU build logs `whisper_backend_init: using CPU backend`.

## 🎯 GPU / CPU compatibility

`scripts/install_whisper_cpp.py` auto-detects your hardware on Windows:

| Hardware | Build chosen |
|---|---|
| NVIDIA GPU (any — GTX, RTX, Quadro, Tesla…) | cuBLAS build — CUDA DLLs bundled, no Toolkit needed |
| AMD / Intel / no discrete GPU | CPU-only build — works on any Windows PC |

Detection runs `nvidia-smi` and, as a fallback, probes for `nvcuda.dll`.
Only a recent NVIDIA driver is required for the cuBLAS path — no CUDA
Toolkit install. `manager.py` prepends the binary's folder to `PATH` when
spawning so the bundled DLLs load cleanly.

**Override flags for `install_whisper_cpp.py`:**
- `--cpu` — force the CPU build even if an NVIDIA GPU is detected
- `--cuda 12.4.0` — pin a specific cuBLAS version

To force CPU-only inference at runtime (after install), add `-ng`
(or `--no-gpu`) to the `args` list in `whisper_server.yaml`.

## 📱 Mobile web app

A FastAPI web interface lives at `https://<host>:8443`. It is a
WhisperFlow-equivalent for the iPhone (and any other browser): one big
button to record, audio streams to the PC as you speak, the transcript
comes back, one tap copies it. An optional second tap polishes through
the local LLM hub (filler-word removal, no rephrasing).

### Where to launch from, where to reach it

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

### First-time setup

#### 1. PC: generate the local HTTPS cert (one-time)

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

#### 2. iPhone: install the webapp as a Home Screen icon

Open the **Cloudflare URL** in Safari (e.g.
`https://voice.<your-domain>`), then **share sheet → Add to Home
Screen** → name it "Voice". Launch from that icon from now on —
iOS treats it as a standalone app and persists mic permission
across launches.

> If you already had a Home Screen icon from before icons shipped (it
> showed a "W" letter instead of the mic glyph), iOS aggressively
> caches the original — you have to **long-press → Remove Bookmark**
> on the old icon and re-add it via the share sheet to pick up the
> new artwork.

> First-time visit on a new device: open the tokenised URL the tray
> copies via **📋 Copy Cloudflare URL** (it includes `?token=…` if
> you've enabled bearer-token auth). The page stashes the token in
> `localStorage` and strips it from the visible URL. From then on
> just open the icon — nothing to type.

Within a single page session the app keeps the mic stream alive
between recordings, so back-to-back records never re-prompt.

### Daily use

Open the home-screen icon → tap the big red **⬤ RECORD** circle →
speak → tap **◼︎ STOP**. The transcript appears, auto-copied to the
clipboard (the **📋 Copy** button briefly flashes green to confirm),
ready to paste anywhere. Optional second tap on **✨ Polish** runs the
transcript through the local LLM hub for filler-word removal,
auto-copies the polished version (same green flash on its Copy button).

The transcript and polished boxes are both editable — fix a misheard
word before polishing, or tweak the polished output before copying.
Edits are sent to the server on the next polish call so History matches
what's on screen. You can also skip recording entirely: paste any text
into the transcript box and tap **✨ Polish** — a text-only session is
created and shows up in History alongside dictated takes. If you only
want to save the pasted text for later (no polish yet), tap
**💾 Save** next to the transcript's Copy button — it creates a
text-only session in History so you can polish or re-copy it from
there, exactly as if you had dictated it. The
**🧽** icon button in the top-right of the header clears both boxes
and the current session so the next record starts fresh. Sits between
**➕ Append** and **🕵️** Incognito.

#### Append mode

A **➕ Append** checkbox sits in the header. With it on, every new
take is glued onto the existing transcript with a blank-line
separator instead of replacing it — useful when you're moving
between locations or apps and want to build up one big transcript
across multiple records before polishing. The toggle is ephemeral
(off on every fresh page load) and exists on every surface: header
checkbox in the webapp, **➕ Append mode** menu item in the tray,
**➕ Append** checkbox on the *Last transcription* row in the tk
window. The tray menu and tk checkbox share one flag so toggling
either keeps both in sync; the webapp toggle is independent.

#### Incognito mode

A **🕵️** icon toggle in the header. When the outline turns blue,
the **next recording** is flagged `incognito=true` server-side and
never appears in the History list — useful for taking a private
note that shouldn't sit on disk for the 30-day retention window.
The session still works normally during its lifetime (record,
transcribe, polish, copy); the moment you hit **🧽 Reset** or
start the next recording, the client sends `DELETE
/api/sessions/{id}` so the folder is gone.

Ephemeral on the client (off on every fresh page load); webapp
only by design — the tk window and tray-hotkey flows don't write
anything to `archive/`, so every desktop take is already
effectively incognito and there's no history list to hide from.

#### Tk window controls

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

#### Silence skip

Whisper hallucinates plausible-sounding text on near-silent input
("Thanks for watching!", "[Music]", a single "you", etc.). Every take
runs through a loudness gate before transcription: clips with peak
RMS below `silence_dbfs_threshold` (default `-50` dBFS, configurable
in `config/webapp_config.json`) skip whisper entirely and report
`🤫 Empty audio (X.X dBFS) — skipped` in the status line, with no
transcript written. False negatives (rejecting actual speech) are
worse than the occasional hallucination slipping through, so the
default is conservative — lower it (e.g. `-55`) if you whisper a lot,
raise it (e.g. `-45`) if hallucinations get through anyway.

While recording, audio is streamed to the PC every second and persisted
to `archive/YYYY/MM/DD/HH-MM-SS-<id>/raw.webm`. If your phone dies or
the connection drops mid-record, the partial recording is still on the
PC — the **📜 History** view's *🔁 Redo* button replays whisper
on any saved take.

#### Rolling transcription

Whisper runs every ~2 s on the audio you've already streamed, so the
transcript box fills in live while you keep talking — no more black
box between "tap Stop" and "see text". The status line shows
`Recording · partial v1 · …`, then `v2`, `v3`, as each pass lands.
When the final `/finish` pass arrives the transcript box gets
replaced wholesale with the canonical version (whisper is a
sliding-context model, so consecutive passes can disagree on earlier
words as the take grows; the *final* pass wins).

Config knobs in `config/webapp_config.json`:

- `partial_interval_seconds` — how often to re-run whisper while you
  talk (default `2.0`; set to `0` to disable rolling transcription
  entirely and fall back to the one-shot-on-stop behaviour).

#### Auto-stop on silence

A **🤖 Auto-stop on silence** toggle in the **⚙️ Settings** panel —
when on, the page watches the mic energy floor and fires Stop after
`auto_stop_silence_ms` of continuous near-silence (default `1500`).
A 500 ms "keep talking to cancel" banner appears first so a thinking
pause doesn't cut you off. The toggle takes effect immediately on
flip (same UX as Translate / Append / Incognito); **💾 Save**
persists it as the default for fresh page loads.

The detector runs on the existing `AnalyserNode` energy floor — no
extra dependencies, no ONNX. A live `🎙️ VAD peak=N (silence trips
≤ 15)` readout in the status line lets you see exactly what your mic
floor is, in case the threshold ever needs tuning.

#### Switching apps while recording

Mobile browsers can't keep a web page recording in the background.
iOS suspends the page and revokes the mic the moment you switch apps
or lock the screen, and there is no web API to capture audio in the
background. (Android Chrome *can* keep a mic-capturing tab alive, but
relying on that would make the two platforms behave differently.)

So the webapp does the next best thing, symmetrically on both: the
moment you background it mid-record, the take is **finalised** — the
audio streamed so far is transcribed and saved to History instead of
silently lost. When you come back, a yellow **▶ Resume** button appears
next to the record button — one tap starts a new take that continues
the same transcript (it force-appends, whatever the **➕ Append** toggle
is set to), so the seam across the app-switch is invisible. The plain
**⬤ RECORD** button still starts a fresh take. Worst case — the page is
discarded before the finalise lands — the streamed chunks are still on
the PC, recoverable via History → **🔁 Redo**.

True background recording on iPhone is only possible from a native
app; see issue #7 (Custom Keyboard Extension spin-off).

### What the status line tells you

The line under the record button reports exactly which step is running
so a long take never feels stuck:

| Phase | Message |
|---|---|
| Live recording | `Recording · 24.3 KB streamed to PC` (live byte counter) |
| Rolling partial landed | `Recording · partial v3 · 41.2 KB streamed` |
| Auto-stop armed | `🤫 silence 640 ms / 1500 ms` |
| Auto-stop firing | `🤖 Auto-stop on silence — keep talking to cancel…` |
| Stop, chunks pending | `Finalising upload · 2 chunks left` |
| Server processing | `Server: ffmpeg → whisper · 1m 4s of audio…` |
| Done | `Done in 3.2 s · 20.0× realtime — tap Copy or Polish` |
| Polish in flight | `LLM hub → gemini_flash · polishing…` |
| Polish done | `Polished in 1.4 s — tap Copy` |

### Translation

Whisper can transcribe speech in any of its supported languages, but
*translation* — speak Spanish, get English back — is a separate task and
the bundled `large-v3-turbo` model has no translation training data, so
turbo + `task=translate` returns garbage.

The fix is two whisper-server instances side-by-side:

- `:8090` — turbo (transcription, fast, GPU). What this repo's
  `whisper_server.yaml` configures.
- `:8091` — a non-turbo, translate-capable model (e.g.
  `ggml-medium.bin`, CPU). Run by the sibling
  [`claude-local-calls`](#-see-also) hub as a lazy-spawn proxy: idle most
  of the time, cold-starts in 3–8 s on the first translate request after
  idle, then unloads after 5 min of inactivity.

Both webapp and tk window expose a **🌐 Translate to English** toggle.
When on, the request routes to `translate_base_url` (default
`http://127.0.0.1:8091`) with `task=translate`. When off, it routes to
the primary whisper-server as usual. The toggle is ephemeral — off on
every launch.

If you don't run the second instance, leave the toggle off and translate
is invisible. The hotkey path always transcribes — there is no F8
translate mode by design.

### Polish models

Defaults to `gemini_flash` — the local-llm-hub alias for Google's
Gemini Flash, routed via the `gemini` CLI on a Google AI Pro
subscription. The polish client allocates a generous `max_tokens`
(16k) to leave room for reasoning chains on models that emit them
(`<think>…</think>`); the hub strips the reasoning server-side before
returning. Other options surfaced in the dropdown:
`claude_haiku`, `claude_sonnet`, `claude_opus` (Claude subscription
via the `claude` CLI), and `gemini_lite`, `gemini_pro` (the other two
Gemini tiers). All six are stable version-free aliases — when the hub
points an alias at a newer display_name nothing in this repo needs
to change. Both surfaces (webapp and tk) expose a dropdown so
you can pick per-take. In the webapp the dropdown lives under
**⚙️ Settings**; in the tk main window it sits inline on the polish
row. **💾 Save** in webapp settings (or **⭐ Save defaults** in the tk
window) persists your choice to `config/webapp_config.json` so the next
launch defaults to it. Both surfaces share that file, so the defaults
sync between them.

The dropdown values come from `config/webapp_config.sample.json` — not
from Python. Adding or removing a hub alias is a one-line JSON edit;
no code change needed. Friendly labels are derived from the alias by
title-casing segments (`gemini_flash` → "Gemini Flash"), so a new
alias also needs no JS change.

### Polish styles

The system prompt is no longer hard-coded — it lives in
`config/polish_prompts.json` as a list of named entries, and the UI
exposes a **Polish style** dropdown alongside the model picker. The
file ships with one entry (`filler-words`) by default:

> You are a transcript polisher. Your only job is to remove filler
> words (uh, um, like, you know, sort of, kind of), false starts, and
> word repetitions. Do NOT summarize. Do NOT rephrase. Do NOT reorder
> sentences. Do NOT add new ideas. Do NOT remove any ideas.

To add a new style (e.g. grammar-only, correctness, raw-idea-to-prompt),
append an entry to `config/polish_prompts.json`:

```json
{
  "id": "grammar-only",
  "label": "Grammar fixes only",
  "description": "Fix spelling, grammar, punctuation. No content changes.",
  "system": "You are a copy editor..."
}
```

Restart the webapp + tk window. The new style appears in both
dropdowns. No Python changes required.

In the webapp's **⚙️ Settings**, a read-only preview shows the system
prompt that will be sent for whichever style is currently selected — a
quick way to verify what the LLM will see. The tk window has a
**👁 Show prompt** button that opens the same preview in a popup.

If the JSON file is missing or invalid, the app falls back to a
hard-coded built-in `filler-words` entry so polish never breaks.

### History and cleanup

Every recording lands in `archive/YYYY/MM/DD/HH-MM-SS-<id>/` with
`raw.webm`, transcoded `audio.wav`, `transcript.txt`, `polished.txt`,
and `meta.json`. The directory is gitignored. Sessions older than 30
days are auto-deleted on app start (configurable in
`webapp_config.json`).

The webapp's History panel is **open by default** so the action row
is always reachable in one tap. It loads the **10 newest** entries
and shows a **📥 Load more** button at the bottom for the next 10.
The summary line reads `📜 History (10/N)` while more pages exist
and collapses to `(N)` once everything is loaded — keeps the page
light even after weeks of daily use.

Three buttons live above the list, all in a single right-aligned row:

| Button | What it does |
|---|---|
| **🔄 Refresh** | Re-fetches the list from the server. Use this on a second device — the work PC, say — to pick up takes you just dictated from your phone into the home tray. |
| **📋 Copy selected** | Concatenates every checked take's full text in chronological order (oldest → newest of the selection) with a blank-line separator and writes the whole bundle to the clipboard. Each item has a checkbox on the left; the newest take is auto-checked on every refresh, so the "just grab the latest" flow stays one click. Tick more boxes above it to bundle older takes. |
| **🗑️ Clean** | Deletes every saved recording with a confirmation prompt. Briefly flashes red on success. |

Each row also has its own three buttons:

- **📋 Copy** — copies the full text from disk, not the 200-char
  preview the list payload carries.
- **🔁 Redo** — re-runs whisper on the saved raw audio. Useful when
  a phone died mid-record and you want to pull the transcript
  afterwards.
- **🗑️ Delete** — confirmation dialog, then `DELETE
  /api/sessions/{id}` removes that one take. Cleaner than nuking
  everything via the top-row Clean button.

### Optional: bearer-token auth (extra layer)

The webapp ships with the auth gate **off** by default — `auth_token`
is `""` in `config/webapp_config.json` and every caller (tk window,
loopback browser, tunnel visitor) reaches the API freely. With
Cloudflare Access in front of your tunnel, that's already a strong
gate. The bearer token adds a second factor on the API itself — even
a caller past the Access policy still needs the token. Turn it on:

```powershell
& .\.venv\Scripts\python.exe scripts\gen_token.py
```

The script writes a strong `secrets.token_urlsafe(32)` into
`webapp_config.json`. From then on:

- **Loopback bypass.** The tk window and any local probe still hit
  the API without the token. Local UX is unchanged.
- **Remote callers must present the token.** They pick it up
  automatically the first time they open a tokenised URL — tray
  menu → **📋 Copy Cloudflare URL** appends `?token=…` to the URL it
  copies. Open that URL once on the phone — the page stashes the
  token in `localStorage` and strips `?token=…` from the visible URL
  so the Home Screen icon stays clean. All later visits authenticate
  from `localStorage`. Nothing to type.
- **Rotation.** `python scripts/gen_token.py --force` writes a fresh
  token. Re-open the new tokenised URL once on each device that
  should keep working. Other devices stop working immediately.
- **Disable.** `python scripts/gen_token.py --clear` returns the
  webapp to no-auth.

After enabling, rotating, or clearing the token, restart the tray
so the new config is loaded.

#### Password gate (companion to the token)

Pasting a long tokenised URL on every fresh device is awkward, and on
iOS PWAs whose `localStorage` is partitioned from Safari's main jar
the token sometimes doesn't carry over. A short password fixes both:

```powershell
& .\.venv\Scripts\python.exe scripts\set_password.py PW
```

(replace `PW` with whatever you want). When set, the webapp shows
a small login overlay any time an API call returns 401:

1. User opens `https://<your-host>` on a fresh device.
2. JS hits `/api/config`, gets 401 (no token in localStorage).
3. Login overlay appears → user types the password.
4. JS posts to `/api/login`; server validates and hands the bearer
   token back.
5. Page stashes the token in `localStorage` and reloads the config.
   From then on the device behaves as if it had pasted the tokenised
   URL.

Failed attempts (wrong password, or a hit while no password is
configured) are logged with the requesting IP to
`webapp/auth.log` in addition to the normal server log.

```powershell
& .\.venv\Scripts\python.exe scripts\set_password.py --clear
```

clears the password (login overlay disappears; only the token gate
remains). The bearer token must be set for the password to do
anything — the password is just a UX wrapper that hands the
existing token back.

### Persistent URL via Cloudflare tunnel

A named Cloudflare tunnel binds a subdomain you own (e.g.
`voice.your-domain.net`) to your home PC, so the public URL stays
the same on every launch — bookmark once, forever. **Free** if you
already own a domain on Cloudflare. The tray brings it up
automatically alongside whisper + the webapp.

Pair it with **Cloudflare Access** (also free for personal use) and
the bearer token, and you have three layers in front of the webapp:

1. Your domain has to be guessed.
2. Cloudflare Access bounces every request to a Google sign-in
   restricted to your email — random scanners never see the app.
3. The bearer token still has to be presented (already on
   localStorage on every device you've used once).

#### One-time setup

```powershell
# 1. Install cloudflared if you haven't already
winget install Cloudflare.cloudflared

# 2. Authenticate cloudflared to your Cloudflare account (browser flow)
cloudflared tunnel login

# 3. Create the named tunnel — writes credentials JSON to
#    %USERPROFILE%\.cloudflared\<UUID>.json
cloudflared tunnel create voice

# 4. Point your subdomain at the tunnel (DNS CNAME, automatically
#    proxied through Cloudflare)
cloudflared tunnel route dns voice voice.your-domain.net

# 5. Copy the sample config and fill in your UUID + hostname
copy webapp\cloudflared.sample.yml webapp\cloudflared.yml
notepad webapp\cloudflared.yml
```

`webapp/cloudflared.yml` is gitignored so your tunnel UUID + hostname
don't end up in the repo. Default `credentials-file` lookup at
`~/.cloudflared/<UUID>.json` works out of the box; only set it
explicitly in the YAML if you stored the credentials JSON somewhere
else.

#### Cloudflare Access policy (recommended)

In the Cloudflare Zero Trust dashboard:

1. **Access → Applications → Add an application → Self-hosted**.
2. Name it `Voice Transcriber`, hostname `voice.your-domain.net`.
3. **Identity providers**: enable Google (or whatever you prefer).
4. **Policy**: name it `Owner only`, action *Allow*, rule
   `Emails → is → roberto.ferraro@gmail.com`.
5. Save. From now on, every request to the public URL bounces
   through a Google sign-in. Anyone not on the email allowlist
   gets a clean 403.

The bearer token still applies on top — Access just gates the
network reachability. Both layers run independently.

#### Daily use

Just `tray.bat`. The tray detects `webapp/cloudflared.yml` and
spawns cloudflared alongside everything else — the public URL is
live as soon as the tray icon turns green. Open
`https://voice.your-domain.net` on the work PC / phone:
Cloudflare Access prompts for Google sign-in the first time, drops
a session cookie, subsequent visits are seamless.

Tray menu items for sharing the URL:

- **📋 Copy local URL** → `https://127.0.0.1:8443` for use on this PC.
- **📋 Copy Cloudflare URL** → `https://voice.your-domain.net` —
  what to paste on the phone or share. Both URLs include
  `?token=…` automatically when bearer-token auth is enabled.

For headless / no-tray use, `webapp_tunnel_named.bat` does the same
work in one foreground process.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_CONNECTION_CLOSED` typing the local URL | Typed `http://...:8443` without the scheme — uvicorn closed the TLS port | Always type `https://127.0.0.1:8443`. For remote use, open the Cloudflare URL via tray → 📋 Copy Cloudflare URL |
| `ERR_NAME_NOT_RESOLVED` on the Cloudflare URL | DNS hasn't propagated, or the domain's nameservers aren't on Cloudflare | `nslookup voice.your-domain.net 1.1.1.1` — empty result means the CNAME isn't on Cloudflare. Verify the domain shows `Active` in dashboard → Websites; if not, switch nameservers at your registrar |
| `401 missing or invalid bearer token` after rotating | Phone has the OLD token in localStorage; server has the new one | Tray → 📋 Copy Cloudflare URL → open on the phone once. The page picks up `?token=…` and refreshes localStorage. If still stuck, Settings → Safari → Advanced → Website Data → remove the site, then re-open |
| Cloudflare Access blocks you | Email not on the policy allowlist | Zero Trust dashboard → Access → Applications → your app → Policies → edit Include rule, add your email, save. Effective immediately |
| `Init failed: Load failed` toast | Network dropped while the page held a stale connection | Pull down on the page to reload — init retries automatically |
| iOS prompts for mic on every record | Loaded from Safari URL bar, not from a Home Screen icon | **Add to Home Screen** and launch from the icon. Or whitelist the site under Settings → Safari → Settings for Websites → Microphone |
| Not sure the phone is running the latest build | iOS cached an old copy of the webapp | Open **⚙️ Settings** — the `Build:` line shows the git SHA + build time the device loaded. Compare with `git rev-parse --short HEAD` on the PC, or `curl -k https://127.0.0.1:8443/api/version`. Asset URLs are content-hash stamped, so a tray restart after an edit always invalidates the cache — no manual cache-buster bumps |
| Polish fails with `502 hub returned…` from the `claude` or `gemini` CLI | Selected polish model's CLI isn't logged in, or the subscription is unreachable | Run `claude /login` (Claude subscription) or `gemini /auth login` (Google AI Pro) on the host running local-llm-hub. Or pick a different model in the dropdown — `gemini_flash` and `gemini_lite` are the cheapest tiers |
| Microphone level meter stays at 0 | iOS routed the mic through Bluetooth headphones at the system level | Disconnect Bluetooth, retry. Or toggle **Force built-in mic** in settings |
| Pasted transcript has weird background colour or styling | The page's styled DOM was leaking into the clipboard alongside the plain text | Resolved — the Copy button writes a single `text/plain` MIME type via `ClipboardItem` |
| Local cert warns in the browser | LAN IP or hostname changed since the cert was generated | Re-run `python scripts/gen_ssl_cert.py` and restart the tray |
| Webapp port `:8443` busy after a crash | Old uvicorn still bound | `Get-NetTCPConnection -LocalPort 8443 -State Listen \| Stop-Process -Id $_.OwningProcess -Force` then restart the tray |
| Tray says `cloudflared not on PATH` | Binary missing | `winget install Cloudflare.cloudflared`, restart the tray |
| Tray boots but no public URL | `webapp/cloudflared.yml` missing | Copy `webapp/cloudflared.sample.yml` to `webapp/cloudflared.yml`, fill in UUID + hostname, restart the tray. See "Persistent URL via Cloudflare tunnel" |

## 🧪 Testing

The repo ships a pytest suite covering the Python modules, the FastAPI
routes, the bearer-token middleware, and an end-to-end smoke test that
boots a real `uvicorn` process. The JS in `app/webapp/static/app.js`
has a tiny Vitest harness too (optional — only runs when Node.js is
installed) plus a Python parity port so the rule stays correct even on
Python-only machines.

### Install the test dependencies

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### Run the suite

```bat
:: Everything, including the slow smoke test (~7 s)
.venv\Scripts\python.exe -m pytest

:: Fast iteration — skip the uvicorn-boot smoke test (~2 s)
.venv\Scripts\python.exe -m pytest -m "not smoke"

:: One module at a time
.venv\Scripts\python.exe -m pytest tests\test_polish.py -v
```

### Playwright browser smoke tests

A small `pytest-playwright` suite under `tests/e2e/` catches SPA boot regressions (JS errors, empty `<select>`s, broken settings toggle, missing login overlay). Runs against the **live tray on `https://127.0.0.1:8443`** — does not boot anything itself; if the tray isn't up, every test is skipped with a clear message.

One-time setup:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m playwright install chromium
```

Then with the tray running (`tray.bat`):

```powershell
.\scripts\run-e2e.ps1
# or directly:
& .\.venv\Scripts\python.exe -m pytest -m smoke -v tests/e2e
```

### Optional: JS tests via Vitest

```bat
npm install
npm test
```

Skipped automatically when Node.js isn't on `PATH` — the Python suite
still covers the same logic via the parity port in
`tests\test_static_app_js.py`.

### What each file covers

| File | What it pins |
|------|--------------|
| `tests\test_webapp_config.py` | First-run defaults come from `webapp_config.sample.json` (not Python); regression guard that no model-name literal sneaks back into `src\webapp_config.py` |
| `tests\test_polish.py` | Hub client request shape, `<think>` stripping, error wrapping |
| `tests\test_polish_prompts.py` | Library load, dedupe, built-in fallback |
| `tests\test_app_config.py` | 100-language Whisper map, ISO normalisation, validation |
| `tests\test_silence.py` | RMS dBFS gate (int16, float, 8-bit, stereo WAV) |
| `tests\test_archive.py` | Dated session folders, hydrate, cleanup |
| `tests\test_vocabulary.py` | Per-language vocab prompts + hot-reload on mtime |
| `tests\test_snippets.py` | Word-boundary keyword expansion + hot-reload |
| `tests\test_transcription_client.py` | whisper-server multipart shape, translate routing |
| `tests\test_webapp_api_basics.py` | `/healthz`, `/api/config` GET+POST, `/api/status` |
| `tests\test_webapp_api_auth.py` | Bearer-token middleware (loopback bypass, header, query string, exempt paths) |
| `tests\test_webapp_api_polish.py` | `/api/polish-text`, `/api/save-text`, `_resolve_model`, `_preview` |
| `tests\test_webapp_api_sessions.py` | Session CRUD, polish-on-session, 404/400/424 paths |
| `tests\test_static_app_js.py` | `polishModelLabel` parity + source pins on `app.js` |
| `tests\test_webapp_smoke.py` | Real `uvicorn` boot, `/healthz` + `/api/config` over HTTP (marked `smoke`) |
| `tests\e2e\test_smoke.py` | Playwright browser-E2E: SPA boots without JS errors, polish-model + polish-style `<select>`s populate, record button visible, settings panel toggles, login overlay DOM wired (marked `smoke`; requires live tray on :8443) |

## 🔗 See also

- [ferraroroberto/claude-local-calls](https://github.com/ferraroroberto/claude-local-calls)
  — the sibling hub for local LLMs. When it's running it already owns
  port 8090 with a whisper-server; set `mode: external` here to reuse
  that instance instead of spawning your own.
- [ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  — upstream inference engine and release artefacts.
