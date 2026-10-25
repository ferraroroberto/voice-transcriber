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
cd voice-transcriber
setup.bat                 :: creates .venv, installs deps, fetches whisper.cpp + model
tray.bat                  :: system-tray mode, global hotkey F8 (tap = toggle, hold = push-to-talk)
```

That's it. The tray launches and, if no server is running yet, spawns the
bundled `whisper-server` with the configured model. On exit it stops the
server it started.

## 📋 Modes of use

| Command                  | What it does                                                              |
|--------------------------|---------------------------------------------------------------------------|
| `tray.bat`               | Resident tray icon + global hotkey (default: `F8` — **tap to toggle**, **hold ≥ 600 ms for push-to-talk**). Result auto-pastes at the caret in the focused window. Day-to-day default. Also boots whisper-server, the webapp on `:8443`, and the Cloudflare tunnel (when `webapp/cloudflared.yml` exists). |
| `webapp.bat`             | Standalone FastAPI webapp on `https://127.0.0.1:8443` — for headless / dev use. The tray spawns this for you in normal use. |
| `webapp_tunnel_named.bat`| Webapp + Cloudflare named tunnel without the tray — for headless boxes. Tray.bat already covers this in normal use. See [Persistent URL via Cloudflare tunnel](docs/remote-access.md#persistent-url-via-cloudflare-tunnel) for the one-time setup. |
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

## 🔁 Restart

`tray.bat --restart` kills the tray subtree, reclaims the webapp port `:8443` by PID scoped to this repo's `.venv`, then starts fresh. It does not touch `:8090` / `:8091` (whisper-server / translate-server, shared with `local-llm-hub`). Confirm the new build is live with `GET http://127.0.0.1:8443/api/version` (`git_sha` matches `HEAD`) or `GET /healthz`.

## ✅ Verifying changes

Before declaring any webapp-touching change done, run the pre-ship gate:

```powershell
C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1
```

It byte-compiles, runs the static, docs, structure, conventions and comment ratchets and the non-e2e pytest suite, then the diff-routed Playwright e2e on a disposable instance. A green run ends with `✅ Ready to ship.` Details: [docs/testing.md](docs/testing.md).

## 📚 Documentation

One line per feature; each links to its reference doc.

| Topic | What it covers |
|---|---|
| [Configuration](docs/configuration.md) | `config/config.json`, the optional companion files (vocabulary, snippets, speaker blocklist, disfluencies), `whisper_server.yaml` (`local` vs `external` mode), which model is serving, GPU / CPU build choice, first-run checklist |
| [Repository layout](docs/layout.md) | The `src/` (logic) + `app/` (UI surfaces) tree, file by file, the declared `webapp-tray` shape and its exceptions |
| [Mobile web app](docs/webapp-usage.md) | Where to launch from, local HTTPS cert, iPhone Home Screen install, daily use, Append / Incognito / Tk controls, silence skip, gain boost, text size, rolling transcription, auto-stop, switching apps, the status line |
| [Translation and polish](docs/polish-and-translation.md) | 🌐 Translate toggle and the second whisper-server, polish models, the polish style library |
| [History, cleanup and logs](docs/history-and-logs.md) | History tab, retention, the persistent activity log, the usage log |
| [Remote access](docs/remote-access.md) | Bearer-token auth (required for the tunnel), password gate, Persistent URL via Cloudflare tunnel, Cloudflare Access policy |
| [Consuming the session API](docs/consuming-the-session-api.md) | The webapp's session API (`POST /api/sessions` → `/chunk` → `/events` → `/finish` → `/retranscribe`, plus single-shot `/upload`) as a supported integration surface for other fleet apps. Same-host callers bypass auth and only need `verify=False` for the loopback cert |
| [Troubleshooting](docs/troubleshooting.md) | Symptom → cause → fix table |
| [iPhone debugging](docs/iphone-debugging.md) | iOS PWA and certificate troubleshooting |
| [Testing](docs/testing.md) | pytest, Playwright browser tests, the pre-ship gate, Vitest, per-file coverage |
| [Webapp architecture](docs/webapp-architecture.md) | Why the webapp is shaped the way it is |
| [Internal structure diagram](docs/architecture.mmd) | Hand-authored Mermaid diagram of modules and data flow |
| [Market scan](docs/market-scan/initial-scan.md) | Voice-dictation market research notes |

## 🔗 See also

- [ferraroroberto/local-llm-hub](https://github.com/ferraroroberto/local-llm-hub)
  — the sibling hub for local LLMs. When it's running it already owns
  port 8090 with a whisper-server; set `mode: external` here to reuse
  that instance instead of spawning your own.
- [ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  — upstream inference engine and release artefacts.
