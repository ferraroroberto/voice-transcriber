# Testing

The repo ships a pytest suite covering the Python modules, the FastAPI
routes, the bearer-token middleware, and an end-to-end smoke test that
boots a real `uvicorn` process. The JS in `app/webapp/static/app.js`
has a tiny Vitest harness too (optional — only runs when Node.js is
installed) plus a Python parity port so the rule stays correct even on
Python-only machines.

## Install the test dependencies

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Run the suite

```bat
:: Everything, including the slow smoke test (~7 s)
.venv\Scripts\python.exe -m pytest

:: Fast iteration — skip the uvicorn-boot smoke test (~2 s)
.venv\Scripts\python.exe -m pytest -m "not smoke"

:: One module at a time
.venv\Scripts\python.exe -m pytest tests\test_polish.py -v
```

## Playwright browser smoke tests

A `pytest-playwright` suite under `tests/e2e/` catches SPA boot regressions (JS errors, empty `<select>`s, broken settings toggle, broken sign-in sheet) plus regression pins for past iPhone-only bugs (cache hygiene, background-finalize, ▶ Resume). Instance selection is guarded by the vendor-verbatim `tests/e2e/_e2e_live_guard.py` (adopted fleet-wide byte-identical): a bare `pytest tests/e2e` with the tray up **refuses to run** with a guard message rather than silently adopting it; with the tray down it boots its own disposable instance instead. Set `VT_E2E_LIVE=1` (`scripts/run-e2e.ps1` does) to explicitly *adopt* the live tray on `https://127.0.0.1:8443` for read-only smoke checks — this repo never kills it to satisfy the opt-in (`tray.bat --restart` owns that). Set `VT_E2E_BASE_URL` to point the suite at a different instance (bypasses the guard, since it's already an explicit choice of target).

Every test runs in **two engine projections**: Chromium-desktop and **WebKit + iPhone** (the iOS-Safari engine family, projected onto an `iPhone 15 Pro Max` viewport). Pin one engine for a faster loop with `--browser chromium`. The record-flow regression tests are marked `desktop_only`: they need Chromium's fake-media-stream flags and skip under the WebKit projection.

One-time setup:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m playwright install chromium webkit
```

Then with the tray running (`tray.bat`):

```powershell
.\scripts\run-e2e.ps1
# or directly:
$env:VT_E2E_LIVE = "1"
& .\.venv\Scripts\python.exe -m pytest -m smoke -v tests/e2e
```

Without a tray, a bare run already boots its own disposable webapp on a free port (the live-tray port being free means nothing to adopt). `--e2e-autoboot` (or `VT_E2E_AUTOBOOT=1`) forces that same disposable path unconditionally, regardless of whether a tray is running — this is what the pre-ship gate uses, so it never touches a live tray.

## Verifying changes before ship

For any change touching the webapp, run the pre-ship gate before declaring it done:

```powershell
C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1
```

It byte-compiles `app src tests`, runs the **static ratchet** (no new duplicate block, unused import/variable, ruff bugbear `B` or security `S` finding beyond `.static-ratchet-baseline.json`; needs Node for `npx jscpd`, and `ruff` from `requirements-dev.txt` in the `.venv`), runs the **docs ratchet** (`README.md` stays within 40 KiB and neither it nor `docs/**/*.md` gains a history marker, meaning an issue reference, a dated heading or a changelog heading, beyond `.docs-ratchet-baseline.json`; a doc states the present-tense why and leaves history to git, PRs and issues), runs the **structure ratchet** (shape `webapp-tray` with `extra_roots = ["webapp"]`; no file past 1,000 lines, Python/JS function past 100 lines, or flat directory past 40 modules, and no `src` module importing `app`, beyond `.structure-ratchet-baseline.json`; a tracked root folder outside the declared shape fails too, never baselined), runs the **conventions ratchet** (no `no-window`, `logging-not-print` or `no-drive-path` rule gains a hit in any file beyond `.conventions-ratchet-baseline.json`; exit 3, a file that could not be parsed, fails as NOT CONFIRMED), runs the **comment ratchet** (no file gains an issue reference in a `.py`, `.js`, `.css` or `.ps1` comment beyond `.comment-ratchet-baseline.json`; exit 3, a file that could not be scanned, fails as NOT CONFIRMED), runs the **instruction ratchet** (no prompt-audit rule gains a violation in `CLAUDE.md` or `AGENTS.md`, and neither passes its size cap, beyond `.instruction-ratchet-baseline.json`; exit 3, a file that could not be read, fails as NOT CONFIRMED), runs the **doc references ratchet** (every repo path, `python -m` module, relative link and loopback port that `README.md`, `CLAUDE.md` and `docs/**/*.md` name resolves against the tracked tree and `.fleet.toml`, beyond `.doc-refs-ratchet-baseline.json`; the other services' ports are listed in `allow_refs`; exit 3, a doc that could not be read, fails as NOT CONFIRMED), runs the **dead code ratchet** (no function, class or method that no tracked Python file references by name, tests included, beyond `.dead-code-ratchet-baseline.json`; routes, dunders and test hooks are built-in entry points; exit 3, a Python file that could not be parsed, fails as NOT CONFIRMED), runs the non-e2e pytest suite, then runs the Playwright e2e suite (Chromium + WebKit/iPhone) against a **disposable webapp it boots on a free port** and tears down. The e2e stage is **routed by the diff** (`scripts/e2e_route.py` over the `.fleet.toml [e2e]` rules): a docs- or backend-only diff skips it and prints the router's reason, a static asset runs the smoke target, one surface runs its slice, and anything the router cannot read (error, empty diff, unclassified path) runs the whole suite; CI always runs the whole suite — so a running tray on :8443 is left untouched, and a forgotten tray can't let a regression pass silently. Exits non-zero on the first failure; a green run ends with `✅ Ready to ship.`

The last stdout line of every run is `GATE-RESULT: PASS` or `GATE-RESULT: FAIL` (an unhandled terminating error is printed on stdout first). Never pipe the gate when its exit code matters, since `... | tail` reports `tail`'s status and turns a red run green; a caller that cannot avoid a pipe asserts on that line, and a run with no such line is not a pass.

**Design inventory.** Every surface an e2e test opens carries one `surface(page, "<tab>.<surface>")` call (the vendored `tests/e2e/_surface.py`, tracked as `surface_capture` in `.fleet.toml [vendored]`). It is a no-op on every normal run. `/design-review measure voice-transcriber --e2e` arms it on a disposable instance (`scripts/design_review_synthetic.py`, never the live tray) and measures each tagged surface in light and dark. A new overlay or state takes one `surface(...)` call in the test that opens it, placed after its assertions and directly in the test body (a call inside a helper does not select the test).

## Optional: JS tests via Vitest

```bat
npm install
npm test
```

Skipped automatically when Node.js isn't on `PATH` — the Python suite
still covers the same logic via the parity port in
`tests\test_static_app_js.py`.

## What each file covers

| File | What it pins |
|------|--------------|
| `tests\webapp\test_webapp_config.py` | First-run defaults come from `webapp_config.sample.json` (not Python); regression guard that no model-name literal sneaks back into `src\webapp_config.py`; `reload_if_changed` serves a file another process rewrote |
| `tests\test_polish.py` | Hub client request shape, `<think>` stripping, error wrapping |
| `tests\test_polish_prompts.py` | Library load, dedupe, built-in fallback |
| `tests\test_app_config.py` | 100-language Whisper map, ISO normalisation, validation |
| `tests\test_silence.py` | RMS dBFS gate (int16, float, 8-bit, stereo WAV) |
| `tests\test_gain.py` | Quiet-environment gain boost — amplitude math, int16 clipping, in-place WAV boost, fail-open on unsupported sample width |
| `tests\test_recording_pipeline.py` | `finalize_transcript` — the strip -> append-merge -> clipboard-copy tail shared by the tray and the tk window |
| `tests\test_archive.py` | Dated session folders, hydrate, cleanup |
| `tests\test_activity_log.py` | Persistent SQLite events log — record, read, count, prune-older-than |
| `tests\test_analytics.py` | Today's usage summary — take count / wpm / time-saved, derived from the activity log |
| `tests\test_audio.py` | ffmpeg webm->wav transcode wrapper, incl. `CREATE_NO_WINDOW` on Windows |
| `tests\test_event_loop.py` | Selector event-loop shim wiring + accept-loop resilience — root cause of the :8443 wedge |
| `tests\test_probe_cache.py` | `ProbeCache` on a fake clock and a stuck probe — fresh/stale/too-old reads, a probe that has not answered is unknown (never up or down), one probe in flight at a time, a failing probe stays unknown |
| `tests\test_process_supervisor.py` | Shared adopt-or-spawn primitives — TCP probe, stop ladder, readiness poll — composed by both `WhisperServerManager` and `WebappManager` |
| `tests\test_whisper_server_manager.py` | `WhisperServerManager` across process boundaries — `stop` / `logs` / `status` work off the PID file and log file a prior process left on disk, not in-memory state |
| `tests\test_e2e_live_guard.py` | Live-tray adoption guard — free-port autoboot vs. refuse-without-opt-in |
| `tests\test_gate_result.py` | The gate's last stdout line is `GATE-RESULT: PASS` or `GATE-RESULT: FAIL` on a pass, a failed stage, a missing `.venv` and an early terminating error (the verdict helper runs for real over captured stdout); exit codes stay 0 and 1 and every `exit` goes through the verdict |
| `tests\test_e2e_route.py` | The gate's e2e-routing stage never narrows on a verdict it can't read — a failing or unreadable classifier runs the full suite with the reason spelled out; a readable `skip` or `full` verdict is honoured |
| `tests\test_e2e_spawn_isolation.py` | The e2e harness's spawned app writes activity/usage data only under the harness's own temp data root, never the real runtime-data folder |
| `tests\test_classify_e2e.py` | Anti-drift guard for this repo's `.fleet.toml` `[e2e]` table — representative paths land in the tier their rule intends |
| `tests\test_static_ratchet_gate.py` | The gate's static-ratchet (and conventions-ratchet) stage maps `scripts/static_ratchet.py`'s exit codes — exit 3 (no `npx`/`ruff`, a timeout) fails as NOT CONFIRMED, never a pass or a skip |
| `tests\test_docs_ratchet_gate.py` | The gate's docs-ratchet stage maps `scripts/docs_ratchet.py`'s exit codes the same way — exit 3 (could not read the docs) fails as NOT CONFIRMED |
| `tests\ratchets\test_ratchet_stages.py` | The gate's comment / instruction / doc-refs / dead-code ratchet stages map their vendored scripts' exit codes the same way, and each stage is declared in `.fleet.toml` with its script vendored and its baseline committed |
| `tests\test_testing_doc_coverage.py` | This table itself — every tracked `tests\**\test_*.py` module has a row here |
| `tests\test_recorder.py` | PortAudio device hot-plug recovery — device-table refresh + retry-once on stream-open failure |
| `tests\test_runtime_files.py` | `src\runtime_files.py` guarded writers refuse a path under a code folder and write anywhere else |
| `tests\test_single_instance.py` | Named-mutex primitive (`app/tray/single_instance.py`) — importable contract + in-process behaviour |
| `tests\test_vocabulary.py` | Per-language vocab prompts + hot-reload on mtime |
| `tests\test_snippets.py` | Word-boundary keyword expansion + hot-reload |
| `tests\test_speaker_label.py` | Strip fabricated leading speaker labels (titled + assistant-name family + blocklist) |
| `tests\test_disfluency.py` | Spoken filler-word strip — built-in list, elongations, punctuation/case repair, config override + hot-reload |
| `tests\test_transcription_client.py` | whisper-server multipart shape, translate routing |
| `tests\test_cli_ensure_server.py` | `BaseCommand._ensure_server` honours the hub-first record gate — record/transcribe keep working when the hub is reachable even if the local whisper-server is down |
| `tests\test_cli_language.py` | `record` / `transcribe --language` accepts the full 100-language Whisper set (ISO code or English name), not just a legacy three-entry tuple |
| `tests\test_cli_record.py` | The CLI `record` command runs takes through `handle_take` — a silent take never reaches whisper, an audible one is transcribed and emitted |
| `tests\test_cli_server_cmd.py` | `server stop` exits 0 once the stop has run; `status` keeps mapping not-running to exit 1 |
| `tests\test_secret_compare.py` | `secrets_equal` accepts non-ASCII text, and no code outside `src\secret_compare.py` calls `compare_digest` |
| `tests\webapp\test_webapp_api_basics.py` | `/healthz`, `/api/config` GET+POST, `/api/status`, cache hygiene + build identity (`/` revalidated, every stamped `/static/*` asset immutable, `?v=<hash>` stamps match on-disk content, `/api/version` shape + `asset_hash` equals the `app.js` stamp) |
| `tests\webapp\test_webapp_status_responsiveness.py` | A stuck `/api/status` probe never holds up `/api/version`, `/api/analytics/summary`, `/api/config` or `/healthz` on the shared event loop, and reports the unanswered probe as `null` within its bounded wait |
| `tests\webapp\test_webapp_api_auth.py` | Bearer-token middleware (loopback bypass, header, query string, exempt paths); `/api/login` lock-out on a fake clock (401s, then 429 with `Retry-After`, success after the wait, the PC never locked) and the guessing alert on a fake notifier (once per streak, no secrets in the text) |
| `tests\test_access_guards.py` | Pre-publish request gates — `publish_refusal_reason` blocks cloudflared from spawning while `auth_token` is empty; `set_password.py`'s password-length floor |
| `tests\webapp\test_webapp_api_activity.py` | `GET /api/activity` — persistent activity log API |
| `tests\webapp\test_webapp_api_analytics.py` | `GET /api/analytics/summary` — usage analytics API |
| `tests\webapp\test_webapp_api_polish.py` | `/api/polish-text`, `/api/save-text`, `_resolve_model`, `_preview` |
| `tests\webapp\test_webapp_api_sessions.py` | Session CRUD, polish-on-session, 404/400/424 paths |
| `tests\webapp\test_webapp_api_usage.py` | The vendored usage log mounted in the webapp — events land in the app's own usage SQLite file behind the bearer-token gate |
| `tests\webapp\test_webapp_compression.py` | The stream-safe gzip middleware wiring — it's the outermost layer, and the session SSE feed still delivers its first chunk at once |
| `tests\webapp\test_webapp_finish_partial.py` | `POST /api/sessions/{id}/finish`'s rolling-partial shortcut — serves the worker's last partial instead of a final whisper pass, plus the served-by stamp it leaves; a translate or different-language request falls through to the real transcription path |
| `tests\test_orphan_css.py` | Every class a first-party `styles*.css` selector names is used by the static HTML/JS or the Python emitting markup (an orphaned rule fails) |
| `tests\test_static_app_js.py` | `polishModelLabel` parity + source pins across the static/ JS module graph |
| `tests\test_take_lifecycle.py` | The take lifecycle in `take.js`, run under Node: every legal move is taken and every other move is refused with one console error and no change; `state.mode` is written only by `take.js` and no reader compares it to a string. The recorder-level exits (self-stop, a throwing `MediaRecorder`, a failed session create, a denied mic) are the Vitest table in `app/webapp/static/__tests__/take.test.js` |
| `tests\webapp\test_webapp_event_language.py` | The `transcribed` event stores `payload.language` as an ISO code on both write paths (upload/finish and the rolling-partial shortcut) while the response and `meta.language` keep the requested spelling |
| `tests\webapp\test_webapp_take_duration.py` | A take's `duration_seconds` is derived from the transcoded WAV when the client omits it (upload, finish, and the rolling-partial shortcut), a client-reported value still wins, and an unreadable WAV leaves it NULL |
| `tests\webapp\test_webapp_smoke.py` | Real `uvicorn` boot, `/healthz` + `/api/config` over HTTP (marked `smoke`) |
| `tests\e2e\test_smoke.py` | Playwright browser-E2E, one test per first-screen flow (marked `smoke`; boots its own disposable instance by default, or adopts a live tray via `VT_E2E_LIVE=1`). Record pane: boots without JS errors, both Polish `<select>`s populate, no sign-in sheet for a signed-in device, the record circle and switch pills render, controls inherit the body font, the header toggles are unpainted glyphs, and the incognito chip and text areas draw the `control-border` token in light and dark. Text card: dictate, Polish (its success toast is neutral), switch views, Clear. Settings: each control saves its own field as it changes, a refused save reverts with an error toast (the only tinted, assertive one), Services/About, the delete-all and Polish-prompt sheets, and the text size steps scale the root font and survive a reload. Sign-in: a wrong password keeps the Esc-proof `<dialog>` open, the right one closes it |
| `tests\e2e\test_page_header.py` | Settings is never a tab: the nav lists only Record and History, every pane opens with the `home-head` row carrying the theme toggle and the Settings gear, the gear reaches Settings from any tab until a tab is chosen, and the build footer ends every tab clear of the phone pill's fade; each pane's context line names only exceptions and never wraps |
| `tests\e2e\test_history_pagination.py` | History on the phone layout: the Latest take card, day groups, Select mode (copies oldest first) and "Show older takes" driven by the server's `has_more` flag; a row tap fetches the take's text and the kebab's Open take shows the take sheet (two-tap Delete); the empty state |
| `tests\e2e\test_viewport.py` | The WebKit projection applies the `iPhone 15 Pro Max` viewport descriptor; on the Chromium desktop window the nav is a left rail, Record and History lay out side by side, both History kebabs open the row menu (Open take, Refresh in place), the picked row's tint sits the card padding in from each border in light and dark, and below the breakpoint the panes stack |
| `tests\e2e\test_rendered_geometry.py` | Rendered geometry through the vendored `_geometry.py` on the 320/390/430/772px x light/dark matrix, walked on one page load: no horizontal overflow on Record, History or Settings; on the touch (WebKit) projection every visible control reaches 44px with no overlapping hit areas |
| `tests\e2e\test_background_finalize.py` | Backgrounding mid-record (`visibilitychange`) finalises the in-flight take instead of dropping it, then Continue this text restarts recording with the earlier transcript in place, and the resumed take is finalised by `pagehide` too (`desktop_only`) |
| `tests\e2e\test_mic_notice.py` | The recorder names the live input, draws the vendored waveform on the take's one stream (a single `getUserMedia`), warns when a muted input delivers no sound and clears it when sound returns; the warning names Remote Desktop for *Remote Audio* (`desktop_only`) |
| `tests\e2e\test_start_failure.py` | Record survives a flaky connection: with the initial `/api/config` load and the first `POST /api/sessions` both aborted, the failure toast shows and the same button then starts a real take whose live partials still subscribe (`desktop_only`) |

