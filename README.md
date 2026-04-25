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
| `tray.bat`               | Resident tray icon + global hotkey (default: `Ctrl+Alt+Space`). Default. |
| `transcribe_voice.bat`   | Classic tkinter main window with Start/Stop server, Record, file pick.   |
| `quick_record.bat`       | One-shot record → transcribe → copy → exit. For Stream Deck.              |
| `quick_record_english.bat` / `quick_record_spanish.bat` | Language-pinned wrappers.     |
| `server.bat start|stop|status|logs` | Direct control of the whisper-server process.             |

Or use the launcher directly:

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
| `spanish`              | Spanish    | Spanish          |
| `spanish-to-english`   | Spanish    | English          |
| `english` *(default)*  | English    | English          |

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

```
transcribe_voice/
├── launcher.py                    # entry point (python launcher.py tray|record|gui|…)
├── setup.bat                      # one-shot: venv + pip + whisper.cpp + model
├── tray.bat                       # default daily launcher
├── transcribe_voice.bat           # main window launcher
├── quick_record*.bat              # Stream-Deck one-shots
├── server.bat                     # raw server start|stop|status|logs
├── requirements.txt
├── .gitignore
├── config/
│   └── config.json                # app config (language, hotkey, mics)
├── cli/
│   ├── main.py                    # argparse dispatcher
│   └── commands/                  # record, transcribe, gui, tray, server
├── core/
│   ├── app_config.py              # AppConfig loader
│   ├── recorder.py                # sounddevice capture
│   └── transcription_client.py    # HTTP → whisper server
├── gui/
│   ├── app.py                     # tkinter main window
│   ├── tray.py                    # pystray + pynput hotkey
│   └── recording_popup.py         # compact VU popup
├── whisper_server/
│   ├── manager.py                 # spawn / kill / health / PID / describe
│   └── whisper_server.yaml        # mode, paths, port, CLI args
├── scripts/
│   ├── install_whisper_cpp.py     # download prebuilt cuBLAS whisper.cpp
│   └── download_model.py          # fetch ggml model from HF
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

## 🎯 CUDA

On Windows, `scripts/install_whisper_cpp.py` grabs the **cuBLAS** prebuilt
from the latest whisper.cpp GitHub release. That zip already bundles the
right CUDA DLLs (`cudart64_*.dll`, `cublas64_*.dll`, `cublasLt64_*.dll`)
right next to `whisper-server.exe` — no CUDA Toolkit install needed, only
a recent NVIDIA driver. `manager.py` prepends the binary's folder to
`PATH` when spawning so those DLLs load cleanly.

To force CPU-only inference, add `-ng` (or `--no-gpu`) to the `args` list
in `whisper_server.yaml`.

## 🔗 See also

- [ferraroroberto/claude-local-calls](https://github.com/ferraroroberto/claude-local-calls)
  — the sibling hub for local LLMs. When it's running it already owns
  port 8090 with a whisper-server; set `mode: external` here to reuse
  that instance instead of spawning your own.
- [ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  — upstream inference engine and release artefacts.
