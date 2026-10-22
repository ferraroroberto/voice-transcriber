# Configuration

Two config files live under the repo: one for the app, one for the server. The model that serves a take, the GPU/CPU build choice and the first-run checks live here too.

## `config/config.json` — app-level

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
  "ptt_threshold_ms": 600,
  "transcribe_base_url": "http://127.0.0.1:8000",
  "translate_base_url": "http://127.0.0.1:8091"
}
```

`language` accepts any of the 100 Whisper-supported languages, either as a
Whisper ISO code (`en`, `es`, `haw`, `yue`) or as the lowercase English
name (`english`, `spanish`, `italian`, …) — narrowed to whatever
`enabled_languages` allows (see below). Common values:

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
- `ptt_threshold_ms` — the hotkey (default `<F8>`) is a single key with two
  modes: tap to toggle (start/stop), or hold ≥ this many ms and release for
  push-to-talk. Default **600 ms** — comfortably above the natural tap
  distribution so a deliberate "press to start" reliably sticks. Lower it
  if you want a snappier PTT; raise it further if a tap still occasionally
  registers as PTT. A second safety net inside the tray ignores any PTT
  release that fires while the recorder has been live for less than 400 ms,
  so a press that races the event pump can't kill a fresh take.
- `transcribe_base_url` — the primary transcription endpoint. Defaults to the
  local-llm-hub on `:8000`, which resolves the hub's `roles.audio.transcribe`
  chain — **parakeet** (Mac ANE) as primary with an automatic **whisper
  failover** — and records the request in the hub's observability ring. No
  `model` is sent, so the hub applies the role rather than a fixed model. If the
  hub process itself is unreachable, transcription **falls back to the local
  whisper-server** the app already runs (the `:8090` it spawns/adopts), so
  dictation is never worse than the pre-hub direct path. Point it at
  `http://127.0.0.1:8090` to bypass the hub and go straight to local whisper.
- `translate_base_url` — the secondary whisper-server URL used when the
  🌐 Translate toggle is on. Defaults to the local-llm-hub's `:8091`
  contract. (Translate keeps its own proven whisper endpoint — it does not
  route through the hub role.) See [Translation](polish-and-translation.md#translation).
- `suppress_hotkey` — when `true` (default), the tray consumes the global
  hotkey via a pynput low-level hook so the focused window never sees the
  keystroke. Tray menu has a 🚫 Suppress hotkey toggle.
- `show_notifications` — when `true`, the tray shows a toast on record/
  transcribe events. Defaults to `false` in the committed config. Tray menu
  has a 🔔 Show notifications toggle.
- `enabled_languages` — narrows the language picker to this list of Whisper
  ISO codes (default `["en", "es", "it"]` in the committed config), out of
  the full 100 Whisper supports (see the `language` note above).

Four optional companion files (all gitignored, sample-tracked):

- `config/vocabulary.json` — per-language buckets of proper nouns, brands,
  and jargon Whisper would otherwise mishear. Joined into the request's
  `prompt` so the decoder biases toward those words. See
  `config/vocabulary.sample.json` for the schema.
- `config/snippets.json` — short keys auto-expanded in the transcript
  before it hits clipboard / caret paste (e.g. `"myemail"` →
  `"you@domain.com"`). Word-boundary, case-insensitive matching. See
  `config/snippets.sample.json`.
- `config/speaker_blocklist.json` — names whisper recurrently hallucinates
  as a leading speaker label glued to the front of real speech. Two layers
  strip these automatically with **no config**: titled labels (`Name, Ph.D.:`,
  `Dr. X:`, `Speaker N:`), and the **misheard-assistant-name family** —
  whisper, primed by local-llm-hub's `Claude Code` boost term, re-emits a
  mangled variant (`Cloud Code`, `Claudio Couto`, `Claudius C.`,
  `Claude Coulson`, …) at the head of a quiet-lead-in take; the committed
  `Cl[ao]ud…` heuristic in `src/speaker_label.py` removes it whether glued by
  a separator or by whitespace alone, while leaving the correctly-spelled tool
  name (`Claude` / `Claude Code`) intact. This object-keyed file is for any
  *other* recurring bare hallucination outside that family, stripped whatever
  separator whisper invents — colon, comma, dash, or period. See
  `config/speaker_blocklist.sample.json`.
- `config/disfluencies.json` — overrides the **filler-word strip**, which runs
  with **no config at all**. The ASR is a verbatim recogniser, so every `uh`
  and `um` you actually said lands in the transcript (measured: ~23 per 1000
  words). `src/disfluency.py` removes `uh`, `um`, `uhm`, `erm`, `hmm`, `mmm`
  and their elongations (`uhh`, `ummm`, …) before the text hits clipboard /
  caret paste — punctuation, spacing and sentence capitals repaired, and a
  take that is *only* fillers left untouched. `oh`, `ah`, `eh`, `er` and bare
  `mm` are **deliberately kept**: they carry meaning in real dictation ("Oh,
  and by the way…"), so removing them would rewrite sentences. Write this file
  to change that — `{"fillers": ["uh", "um", "oh"]}` replaces the built-in
  list, `{"enabled": false}` restores fully verbatim transcripts. See
  `config/disfluencies.sample.json`. For what a regex can't fix — false starts
  and repetitions — use the LLM [polish](polish-and-translation.md#polish-styles) pass.

All four hot-reload on mtime change — no restart needed after editing.

Hotkey uses [`pynput.keyboard.GlobalHotKeys`](https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys)
syntax: angle-bracketed modifiers + a key, `+`-separated.

## `src/whisper_server/whisper_server.yaml` — server-level

The key knob is **`mode`**:

| `mode`     | Behaviour on startup                                                             |
|------------|----------------------------------------------------------------------------------|
| `local`    | **This project owns the server.** If `host:port` is already in use by something else, refuse to start and surface a clear error. Otherwise spawn the bundled `whisper-server`. Stop it on exit. |
| `external` | **Reuse an already-running server if present** (e.g. one started by the sibling [`local-llm-hub`](https://github.com/ferraroroberto/local-llm-hub) hub). If nothing is listening, spawn our own binary. Never stops a server it didn't start. |

Switch by editing the top of `src/whisper_server/whisper_server.yaml`:

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

That's the *locally-configured* model. When `transcribe_base_url` points at
the local-llm-hub (the default), the hub can route a take to a different
backend entirely — e.g. Parakeet on a Mac Mini, falling back across its
`roles.audio.transcribe` chain. The **webapp** shows which backend
actually served each take (e.g. `Parakeet · Mac Mini M4`), read from the
hub's `X-Hub-Served-Model`/`X-Hub-Served-Host` response headers and stored
on the take (`served_model` / `served_host` in its `meta.json` and the
`/api/sessions` list): open the take's sheet in **History** and read
*Served by*. Takes made before that was stored show *Not recorded*.

## 🧪 First run checklist

```bat
REM 1. Binaries + model present?
dir vendor\whisper.cpp\whisper-server.exe
dir vendor\whisper.cpp\models\ggml-large-v3-turbo.bin

REM 2. Server comes up?
server.bat start
server.bat status

REM 3. Quick record test (tap F8 to start, F8 again to stop)
tray.bat
```

If the server doesn't come up, `server.bat logs` prints what
`whisper-server` printed on startup (backend, model-load lines, system
info). The output is kept in `.whisper_server.log` beside the PID file, so
it stays readable after `server.bat start` exits, and `server.bat stop`
can stop a server an earlier `server.bat start` launched. A CUDA-backed build will log `whisper_backend_init: using CUDA
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

