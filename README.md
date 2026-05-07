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
tray.bat                  :: system-tray mode, global hotkey Ctrl+Alt+Space
```

That's it. The tray launches and, if no server is running yet, spawns the
bundled `whisper-server` with the configured model. On exit it stops the
server it started.

## 📋 Modes of use

| Command                  | What it does                                                              |
|--------------------------|---------------------------------------------------------------------------|
| `tray.bat`               | Resident tray icon + global hotkey (default: `Ctrl+Alt+Space`). Day-to-day default. Also boots the mobile webapp on `:8443` when enabled. |
| `webapp.bat`             | Standalone FastAPI webapp on `https://127.0.0.1:8443` — for headless / dev use. The tray spawns this for you in normal use. |
| `webapp_tunnel.bat`      | Webapp + Cloudflare quick tunnel — public HTTPS URL for use from outside Tailscale (e.g. at the office). |
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
  "hotkey": "<F10>",
  "auto_copy": true,
  "auto_start_server": false,
  "log_level": "INFO"
}
```

`language` selects the dictation mode — one of:

| value                  | dictate in | clipboard output |
|------------------------|------------|------------------|
| `english` *(default)*  | English    | English          |
| `spanish`              | Spanish    | Spanish          |

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
├── tray.bat                       # default daily launcher (also boots the webapp)
├── webapp.bat                     # standalone FastAPI webapp launcher
├── webapp_tunnel.bat              # webapp + Cloudflare quick tunnel
├── server.bat                     # raw server start|stop|status|logs
├── requirements.txt
├── .gitignore
├── src/                           # ── LOGIC layer (no UI imports) ──
│   ├── app_config.py              # AppConfig loader
│   ├── recorder.py                # sounddevice capture
│   ├── transcription_client.py    # HTTP → whisper server
│   ├── diagnostics.py             # log ring + port-owner introspection
│   ├── polish.py                  # local-llm-hub client (filler-word polish)
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
│   ├── webapp_config.json         # gitignored — polish model, retention, mic prefs
│   └── webapp_config.sample.json  # committed schema example
├── docs/
│   └── 2026-05-07-mobile-webapp-and-repo-cleanup.md   # design doc
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
| Sitting at the PC | `tray.bat` (or already running) | `https://127.0.0.1:8443` |
| iPhone, on tailnet | `tray.bat` on the PC | `https://<pc-tailscale-name>:8443` |
| iPhone, no tailnet (work) | `webapp_tunnel.bat` on the PC | URL printed by cloudflared, also written to `webapp/last_tunnel_url.txt` |
| Headless box / dev | `webapp.bat` | `https://127.0.0.1:8443` |

In the daily flow you never touch `webapp.bat`. The tray adopts-or-spawns
uvicorn the same way it adopts-or-spawns whisper-server. To opt out, set
`"webapp": {"enabled": false}` in `config/config.json`.

### First-time setup (one-time per device)

The webapp uses HTTPS with a self-signed CA so iOS Safari will allow
microphone access. Three one-time steps to make a phone "remember"
everything:

#### 1. PC: generate the certificate

```powershell
& .\.venv\Scripts\python.exe scripts\gen_ssl_cert.py
```

This writes `webapp/certificates/{ca.pem,cert.pem,key.pem}`, copies the
iOS profile (`voice-transcriber-ca.mobileconfig`) and Android-friendly
DER (`ca.crt`) into `app/webapp/static/`, and installs the CA into the
Windows user trust store via `certutil` (no admin required). The cert
covers `127.0.0.1`, your LAN IP, your Tailscale IPv4, `localhost`, your
hostname, and your tailnet DNS name. Valid 10 years. Re-run this if any
of those addresses change.

Then restart the tray (right-click tray icon → Quit, then `tray.bat`)
so uvicorn picks up the cert. From then on it runs HTTPS automatically
whenever the cert files exist.

#### 2. iPhone: trust the CA (so Safari doesn't warn)

1. Open Tailscale on your iPhone, confirm the green dot.
2. Safari → `https://<pc-tailscale-name>:8443/install-ca`
   (e.g. `https://tower.tail1121fd.ts.net:8443/install-ca`).
   First connection may show "Not Secure" — tap **Advanced → Proceed**
   once to download the profile.
3. iOS shows "Profile Downloaded".
4. **Settings → General → VPN & Device Management** → tap
   *Voice Transcriber Trust* → **Install** (Face ID + passcode).
5. **Settings → General → About → Certificate Trust Settings** →
   toggle on **Voice Transcriber Local CA**.
6. Reload `https://<pc-tailscale-name>:8443` — green padlock, no
   warnings, ever.

> **Always type `https://`** when entering the URL by hand. Without the
> scheme, Chrome/Safari default to `http://` and uvicorn (which is
> serving TLS) will close the connection — you'll see
> `ERR_CONNECTION_CLOSED`.

#### 3. iPhone: persistent microphone permission

Mobile Safari prompts for the mic on every fresh page load by default.
Two ways around that:

**Best — Add to Home Screen (PWA mode).**
In Safari, share sheet → **Add to Home Screen** → name it (e.g.
"Voice"). Launch from that icon from now on. iOS treats it as a
standalone app and persists mic permission across launches —
WhisperFlow-style.

**Belt + suspenders — whitelist the site.**
Settings → Safari → Settings for Websites → **Microphone** → tap your
host → set to **Allow**. Same for **Camera** while you're there.

Within a single page session the app already keeps the mic stream alive
between recordings, so back-to-back records never re-prompt regardless
of these steps.

### Daily use

Open the home-screen icon → tap the big red **⬤ RECORD** circle →
speak → tap **◼︎ STOP**. The transcript appears, auto-copied to the
clipboard, ready to paste anywhere. Optional second tap on **✨ Polish**
runs the transcript through the local LLM hub for filler-word removal,
auto-copies the polished version.

While recording, audio is streamed to the PC every second and persisted
to `archive/YYYY/MM/DD/HH-MM-SS-<id>/raw.webm`. If your phone dies or
the connection drops mid-record, the partial recording is still on the
PC — the **📜 History** view's *🔁 Re-transcribe* button replays whisper
on any saved take.

### What the status line tells you

The line under the record button reports exactly which step is running
so a long take never feels stuck:

| Phase | Message |
|---|---|
| Live recording | `Recording · 24.3 KB streamed to PC` (live byte counter) |
| Stop, chunks pending | `Finalising upload · 2 chunks left` |
| Server processing | `Server: ffmpeg → whisper · 1m 4s of audio…` |
| Done | `Done in 3.2 s · 20.0× realtime — tap Copy or Polish` |
| Polish in flight | `LLM hub → claude-haiku-4-5 · polishing…` |
| Polish done | `Polished in 1.4 s — tap Copy` |

### Polish models

Defaults to `gemma4-e4b-it` (smallest, fastest model in
[`local-llm-hub`](#-see-also)). Larger options: `gemma4-26b-a4b-it`,
`claude-haiku-4-5`. The dropdown in the polish row lets you pick
per-take. The **⭐** button persists your choice to
`config/webapp_config.json` so the next launch defaults to it.

The polish prompt is hard-coded:

> Remove filler words (uh, um, like, you know, sort of, kind of), false
> starts, and word repetitions. Do not summarize. Do not rephrase. Do
> not reorder sentences. Do not add new ideas. Do not remove any ideas.

The same polish step is available in the tk main window
(`python launcher.py gui`) — both surfaces share `webapp_config.json`,
so setting a default from one syncs to the other.

### History and cleanup

Every recording lands in `archive/YYYY/MM/DD/HH-MM-SS-<id>/` with
`raw.webm`, transcoded `audio.wav`, `transcript.txt`, `polished.txt`,
and `meta.json`. The directory is gitignored. Sessions older than 30
days are auto-deleted on app start (configurable in
`webapp_config.json`). A **🗑️ Clean all** button in the History view
nukes everything.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_CONNECTION_CLOSED` on iPhone | Typed without `https://` — Chrome tried HTTP, uvicorn closed the TLS port | Type the full `https://...:8443` URL |
| `Init failed: Load failed` toast | Tailscale on iPhone briefly dropped while Safari held a stale TLS connection | Pull down on the page to reload — init now retries automatically and falls back to defaults |
| iOS prompts for mic on every record | Loaded from Safari URL bar, not from a Home Screen icon | **Add to Home Screen** and launch from the icon. Or whitelist the site under Settings → Safari → Settings for Websites → Microphone |
| Polish fails with `502 hub returned…upstream :8086 unreachable` | Selected polish model's local llama-server isn't running | Start it from the local-llm-hub tray (Models submenu → toggle on), or pick `claude-haiku-4-5` in the dropdown — that one routes via your Claude subscription and doesn't need a local backend |
| Microphone level meter stays at 0 | iOS routed the mic through Bluetooth headphones at the system level | Disconnect Bluetooth, retry. Or toggle **Force built-in mic** in settings (best-effort — iOS doesn't always expose enough info to override its routing) |
| Pasted transcript has weird background colour or styling | The page's styled DOM was leaking into the clipboard alongside the plain text | Resolved — the Copy button now writes a single `text/plain` MIME type via `ClipboardItem` and clears any active selection first |
| Cert worked yesterday, browser warns today | Your LAN IP or Tailscale name changed since the cert was generated | Re-run `python scripts/gen_ssl_cert.py` and restart the webapp |
| Webapp port `:8443` busy after a crash | Old uvicorn still bound | `Get-NetTCPConnection -LocalPort 8443 -State Listen \| Stop-Process -Id $_.OwningProcess -Force` then restart the tray |

## 🔗 See also

- [ferraroroberto/claude-local-calls](https://github.com/ferraroroberto/claude-local-calls)
  — the sibling hub for local LLMs. When it's running it already owns
  port 8090 with a whisper-server; set `mode: external` here to reuse
  that instance instead of spawning your own.
- [ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  — upstream inference engine and release artefacts.
