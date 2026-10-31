# Repository layout

The repo follows the monorepo's `src/` (logic) + `app/` (UI surfaces) split — same convention used by `local-llm-hub`, `grocery-shopping-automation`, and `facilitation-shuffle`. Its shape is `webapp-tray` (declared in `.fleet.toml`); the canonical tree and the placement rules for new code are in project-scaffolding's `docs/repo-layout.md`, and the structure ratchet holds this repo to them. The exceptions are listed at the end.

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
│   ├── activity_log.py            # SQLite events log, 365-day retention
│   ├── analytics.py               # today's take count / wpm / time-saved
│   ├── usage_log.py               # vendored: local view/tap usage log (router + SQLite)
│   ├── webapp_config.py           # typed loader for config/webapp_config.json
│   ├── vocabulary.py              # per-language vocab prompts + hot-reload
│   ├── snippets.py                # word-boundary keyword expansion + hot-reload
│   ├── silence.py                 # RMS dBFS gate
│   ├── gain.py                    # quiet-env gain boost — post-silence-gate
│   ├── speaker_label.py           # strip fabricated leading speaker labels
│   ├── disfluency.py              # strip spoken filler words (uh/um) + hot-reload
│   ├── static_versioning.py       # content-hash asset URLs (JS: whole import graph)
│   ├── recording_pipeline.py      # silence-gate -> gain-boost -> transcribe
│   ├── hot_reload_json.py         # shared mtime-cached JSON loader
│   ├── inject.py                  # clipboard + caret paste
│   ├── mic_glyph.py               # runtime state-tinted tray renderer
│   ├── tunnel.py                  # cloudflared spawn/persist/stop
│   ├── process_supervisor.py      # shared adopt-or-spawn primitives (whisper + webapp managers)
│   └── whisper_server/
│       ├── manager.py             # spawn / kill / health / PID / describe
│       └── whisper_server.yaml    # mode, paths, port, CLI args
├── app/                           # ── UI surfaces ──
│   ├── gui/
│   │   ├── app.py                 # tkinter main window: composition + window lifecycle
│   │   ├── server_panel.py        # status traffic light, model line, start/stop
│   │   ├── transcript_panel.py    # last-transcription box, Copy/Reset/Append
│   │   ├── polish_panel.py        # polish row: style/model pickers, polished text
│   │   ├── tray.py                # pystray icon/menu, event pump, record->transcribe->notify
│   │   ├── hotkey_controller.py   # PTT tap/hold state machine, pynput listener
│   │   ├── service_supervisor.py  # whisper-server + webapp + cloudflared lifecycles
│   │   ├── recording_popup.py     # compact VU popup
│   │   └── diagnostics_window.py
│   ├── cli/
│   │   ├── main.py                # argparse dispatcher
│   │   └── commands/              # record, transcribe, gui, tray, server
│   ├── tray/
│   │   └── single_instance.py     # Windows mutex — one tray at a time
│   └── webapp/                    # FastAPI mobile-first web UI
│       ├── server.py              # wiring: create_app(), lifespan, static mount
│       ├── manager.py             # adopt-or-spawn for uvicorn (used by tray)
│       ├── middleware.py          # bearer-token auth, loopback bypass
│       ├── audio.py               # ffmpeg webm->wav transcode
│       ├── partial_worker.py      # rolling ~2s partial transcription
│       ├── event_loop.py          # selector event-loop shim (Windows :8443 wedge fix)
│       ├── routers/               # sessions · auth · config · misc · activity · analytics
│       │   └── _helpers.py        # shared router helpers
│       └── static/
│           ├── index.html         # tabbed SPA (Record · History; Settings opens from the header gear), big-button mobile-first
│           ├── app.js             # module wiring / boot sequence
│           ├── recorder.js        # MediaRecorder record/upload/finish state machine
│           ├── level.js           # VU meter + VAD auto-stop
│           ├── vad.js             # pure VAD helpers (Silence length → trigger ms)
│           ├── labels.js          # pure label helpers (polish-model alias → display label)
│           ├── partials.js        # rolling-transcription SSE stream
│           ├── wakelock.js        # screen wake lock during recording
│           ├── api.js             # fetch wrappers for server endpoints
│           ├── state.js           # shared runtime state
│           ├── ui.js              # DOM helpers and rendering
│           ├── config.js          # client-side config constants
│           ├── history.js         # session history display
│           ├── polish.js          # polish-text UI flow
│           ├── styles.css         # fleet design tokens (light + dark), recorder and shared rules
│           ├── styles-views.css  # History, Settings and the wide layout; loaded after styles.css
│           └── _vendored/         # fleet components, byte-verbatim from project-scaffolding
│                                  #   (base · nav · home-head · toast · text-size · segmented · usage-log · icons · card · switch · select-native · modal · empty-state · button)
├── assets/                        # static files the apps ship: tray icon, Stream Deck icon
├── webapp/
│   └── cloudflared.sample.yml     # committed template for the gitignored webapp/cloudflared.yml (named tunnel)
├── config/
│   ├── config.json                # app config (language, hotkey, mics, webapp section)
│   ├── polish_prompts.json        # committed — polish-style library (system prompts)
│   ├── webapp_config.json         # gitignored — polish model/style, retention, mic prefs
│   └── webapp_config.sample.json  # committed schema example
├── docs/
│   ├── configuration.md           # config.json, whisper_server.yaml, model, GPU/CPU, first run
│   ├── layout.md                  # this tree
│   ├── webapp-usage.md            # mobile web app: setup, daily use, per-feature behaviour
│   ├── polish-and-translation.md  # translate toggle, polish models and styles
│   ├── history-and-logs.md        # History tab, retention, activity log, usage log
│   ├── remote-access.md           # bearer token, password gate, Cloudflare tunnel + Access
│   ├── troubleshooting.md         # symptom -> cause -> fix
│   ├── testing.md                 # pytest, Playwright, pre-ship gate, per-file coverage
│   ├── webapp-architecture.md     # webapp design & architecture record
│   ├── architecture.mmd           # hand-authored internal-structure diagram
│   ├── consuming-the-session-api.md  # session API integration guide
│   ├── iphone-debugging.md        # iOS PWA / cert troubleshooting
│   └── market-scan/               # market research notes
├── scripts/
│   ├── install_whisper_cpp.py     # download prebuilt cuBLAS whisper.cpp
│   ├── download_model.py          # fetch ggml model from HF
│   ├── gen_ssl_cert.py            # self-signed CA + iOS .mobileconfig
│   ├── gen_token.py               # generate/rotate/clear the bearer auth token
│   ├── set_password.py            # set/clear the login-overlay password
│   ├── gen_app_icons.py           # regenerate PWA/favicon/Stream Deck icons from the fleet brand SVG
│   ├── run_named_tunnel.py        # headless uvicorn + named cloudflared tunnel (no tray)
│   ├── design_review_synthetic.py # throwaway webapp for `/design-review` (synthetic and `--e2e` runs)
│   ├── run-e2e.ps1                # Playwright e2e suite against the live tray
│   ├── static_ratchet.py          # vendored static-analysis ratchet (duplicates, unused imports/variables)
│   ├── static_ratchet_gate.py     # the gate's wrapper around it: exit 3 (a detector could not run) fails
│   ├── docs_ratchet.py            # vendored docs ratchet (README size, history markers in README/docs)
│   ├── docs_ratchet_gate.py       # the gate's wrapper around it: exit 3 (the docs could not be read) fails
│   ├── structure_ratchet.py       # vendored structure ratchet (file size, function length, flat dirs, src→app imports, root layout)
│   ├── conventions_ratchet.py     # vendored conventions ratchet (no-window, logging-not-print, no-drive-path)
│   ├── conventions_ratchet_gate.py # the gate's wrapper around it: exit 3 (a file could not be parsed) fails
│   ├── comment_ratchet.py         # vendored comment ratchet (issue references in product-code comments)
│   ├── comment_ratchet_gate.py    # the gate's wrapper around it: exit 3 (a file could not be scanned) fails
│   ├── instruction_ratchet.py     # vendored instruction ratchet (prompt-audit rules and size caps over CLAUDE.md / AGENTS.md)
│   ├── instruction_ratchet_gate.py # the gate's wrapper around it: exit 3 (an instruction file could not be read) fails
│   ├── doc_refs_ratchet.py        # vendored doc references ratchet (paths, modules and ports named in README / CLAUDE.md / docs)
│   ├── doc_refs_ratchet_gate.py   # the gate's wrapper around it: exit 3 (a doc could not be read) fails
│   ├── dead_code_ratchet.py       # vendored dead code ratchet (functions, classes and methods nothing references)
│   ├── dead_code_ratchet_gate.py  # the gate's wrapper around it: exit 3 (a Python file could not be parsed) fails
│   ├── e2e_route.py               # the gate's wrapper around classify_e2e.py: any unreadable verdict runs the full e2e suite
│   └── verify-before-ship.ps1     # pre-ship gate: byte-compile, static, docs, structure, conventions, comment, instruction, doc references and dead code ratchets, pytest, diff-routed disposable-instance e2e
├── archive/                       # gitignored runtime data — sessions
└── vendor/                        # gitignored, populated by setup.bat
    └── whisper.cpp/
        ├── whisper-server(.exe)
        ├── *.dll                  # CUDA runtime (Windows, cublas build)
        └── models/
            └── ggml-large-v3-turbo.bin
```

## Exceptions

Each is declared once in `.fleet.toml` and here; none is debt.

- `webapp/` (`extra_roots` in `.fleet.toml`): the committed `cloudflared.sample.yml` template; the tray reads the gitignored `webapp/cloudflared.yml` copied from it (path in `app/gui/tray.py`, steps in `docs/remote-access.md`). The `webapp-tray` preset lists no such root, and moving the path would break existing installs' tunnel setup, so the folder stays.
- `app/gui/`: a desktop tkinter window and the tray icon live beside the web UI, because the app has two native surfaces (the tray and the window) as well as the PWA. The preset's `app/tray/` holds only the single-instance guard; the tray menu, hotkey state machine and service supervisor are in `app/gui/`.
- The baseline `.structure-ratchet-baseline.json` is empty: no file, function or directory is over its limit, so any new offender fails the gate. The webapp API tests live in `tests/webapp/` to keep `tests/` under the module limit.
