# 2026-05-07 — Manual test plan

What to test before committing. Roughly 15–25 minutes total. Each
section is independent — you can stop at any point and pick up later.

## 0. Pre-flight (1 min)

Quit the **currently running tray** before testing the new code:

- Right-click the tray icon → **Quit**.
- Verify `.tray.pid` is gone:
  ```powershell
  Test-Path E:\automation\voice-transcriber\.tray.pid
  ```
  Should print `False`.
- Make sure the local-llm-hub is up on `:8000` (you usually have it
  running). Spot check:
  ```powershell
  Invoke-WebRequest -Uri http://127.0.0.1:8000/v1/models -UseBasicParsing | Select-Object StatusCode
  ```

## 1. Tray (smoke, 2 min)

```powershell
.\tray.bat
```

Expectations:
- Tray icon appears, no console window.
- Notification (or just log) about whisper-server starting if it wasn't running.
- New menu items visible:
  - 🌐 webapp :8443 (disabled label)
  - 📋 Copy mobile URL
  - 🔄 Restart web app
- Within ~10 s, this returns 200:
  ```powershell
  Invoke-WebRequest -Uri https://127.0.0.1:8443/healthz -SkipCertificateCheck
  ```

Click **📋 Copy mobile URL** → check your clipboard, you should have
`https://127.0.0.1:8443` (or whichever bind host).

## 2. Hotkey + tk window (2 min)

- Press F10 anywhere → recording popup, speak, press F10 again → toast
  "📋 Copied to clipboard" + transcript on the clipboard.
- Tray menu → **🪟 Open window**. Window opens.
- New row visible at the bottom: **✨ Polish:** with a model dropdown,
  ⭐ Default, ✨ Polish button, polished text area, 📋 Copy polished.
- The dropdown shows: `gemma4-e4b-it`, `gemma4-26b-a4b-it`,
  `claude-haiku-4-5`. Default is `gemma4-e4b-it`.
- After a successful F10 recording, **✨ Polish** is enabled. Click it.
  - If `gemma4-e4b-it` backend is up → polished text appears.
  - If down → MessageBox with the 502 from the hub. (Try a different
    model in the dropdown then re-tap Polish.)
- Tap **⭐ Default** with a different model selected → confirm dialog.
  Check `config/webapp_config.json` was updated:
  ```powershell
  Get-Content config\webapp_config.json
  ```

## 3. Webapp from this PC's browser (3 min)

- Open https://127.0.0.1:8443 in Chrome/Edge.
  - Cert should be trusted (Windows trust install ran in Phase 3).
  - You see the big red **⬤ RECORD** circle.
- Tap → grant mic permission → speak ~5 s → tap **◼︎ STOP**.
  - "Uploading…" → "Transcribing…" → transcript appears below.
- Tap **📋 Copy** under transcript → "✓ Copied". Paste somewhere to verify.
- Pick `claude-haiku-4-5` from the polish dropdown → tap **✨ Polish**.
  - Polished text appears below.
- Tap **📋 Copy** under polished → "✓ Copied". Paste to verify.
- Open the **📜 History** accordion → see one entry. Tap **🔁 Re-transcribe**
  → transcript should regenerate (same text, since the WAV is the same).
- Tap **🗑️ Clean all** → confirm → list empties.
- Open the ⚙️ settings panel:
  - Pick a different polish model → **💾 Save** → close → reopen → confirm
    the dropdown reflects the new default.
  - Toggle **Force built-in mic** → save.
  - Confirm `config/webapp_config.json` reflects all changes.

## 4. Webapp from your iPhone over Tailscale (5 min)

Pre-condition: Tailscale connected on both devices.

- On the iPhone, open Safari → `https://tower.tail1121fd.ts.net:8443`
  (or your tailnet name + `:8443`).
  - First time: tap link **/install-ca** at the bottom of the settings
    panel.
  - iOS prompts to download the profile.
  - Settings → General → VPN & Device Management → tap **Voice
    Transcriber Trust** → **Install** (Face ID).
  - Settings → General → About → Certificate Trust Settings → flip
    switch on **Voice Transcriber Local CA**.
  - Reload the page in Safari → no warning.
- Tap "Add to Home Screen" from Safari's share sheet (optional — gives
  you a WhisperFlow-style standalone icon).
- Tap **⬤ RECORD**. Speak ~10 s. Tap **◼︎ STOP**.
  - Watch the recording live: you'll see chunks land in
    `archive/2026/05/07/<id>/raw.webm` *while* recording (open it in
    Explorer). Confirms the crash-recovery story.
  - Transcript appears.
- Tap **📋 Copy**. Tap once into Messages or Notes on the iPhone and
  paste → text is there.
- Tap **✨ Polish**. Polished text appears, auto-copied.
- Open **📜 History** → tap **🔁 Re-transcribe** on the entry → confirm
  it replays.

## 5. Cloudflare tunnel (only if you want to test work-mode now, 3 min)

```powershell
.\webapp_tunnel.bat
```

Expectations:
- Console shows uvicorn starting (or "Adopting existing webapp" if the
  tray already started one).
- Console shows cloudflared boot logs.
- A line like:
  ```
  📡 Tunnel URL → ...\webapp\last_tunnel_url.txt
     https://wonderful-cat-1234.trycloudflare.com
  ```
- Open the URL on your phone (off Tailscale, or just a different
  browser). Cloudflare provides HTTPS, so no cert warnings.
- Record + transcribe should work. (Polish too if your hub is reachable
  from inside the local request — it is, because cloudflared just
  forwards into your local uvicorn.)
- Press Ctrl+C in the bat console → both processes stop. The
  `last_tunnel_url.txt` file is removed (so future tray reads return
  nothing stale).

## 6. Cleanup teardown (1 min)

- Quit the tray from its menu.
- Verify nothing is left listening on :8443:
  ```powershell
  Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue
  ```
  Should print nothing.
- Confirm whisper-server stopped if the tray spawned it (the tray only
  stops servers it owns):
  ```powershell
  Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
  ```
- The CLI still works:
  ```powershell
  & .\.venv\Scripts\python.exe launcher.py server status
  ```

## What to inspect before committing

- `git status` — long file list, mostly moved files. The interesting
  *new* files are:
  - `app/webapp/` (whole folder)
  - `src/polish.py`, `src/archive.py`, `src/webapp_config.py`
  - `scripts/gen_ssl_cert.py`, `scripts/run_tunnel.py`
  - `webapp.bat`, `webapp_tunnel.bat`
  - `config/webapp_config.sample.json`
  - `docs/mobile-webapp-and-repo-cleanup.md`
  - `docs/manual-test-plan.md` (this file)
- `git diff` on `app/cli/`, `app/gui/`, `src/` — should be just import
  rewrites + tray polish-row + AppConfig webapp section.
- `archive/`, `webapp/certificates/`, `app/webapp/static/voice-transcriber-ca.mobileconfig`,
  `app/webapp/static/ca.crt`, `config/webapp_config.json` — all gitignored.

## Known caveats

- **iOS mic-routing.** When Bluetooth headphones are paired, iOS
  sometimes routes mic input through them despite the **Force built-in
  mic** toggle. This is iOS-system-level and not solvable from the web
  side — best-effort label match is the limit. If you see the level
  meter staying at 0, disconnect Bluetooth and retry.
- **Cert renewal.** `webapp/certificates/cert.pem` is valid 10 years
  but only covers the IPs at generation time. If your tailnet name or
  LAN IP changes (new router, etc.), re-run `python
  scripts/gen_ssl_cert.py` and restart the webapp.
- **gemma4-e4b-it backend.** During Phase 2 testing the gemma4-e4b
  llama-server on `:8086` was down, so the webapp's polish hit a 502
  from the hub. Either start it from the hub's tray, or use
  `claude-haiku-4-5` as the polish default if you want claude quality.
- **Cloudflare quick tunnels.** No auth on the public URL. Anyone with
  the URL can record/transcribe/polish. Tunnel is short-lived (closes
  on Ctrl+C), but treat the URL as sensitive while it's up. A bearer-
  token middleware was scoped but deliberately not enforced on inbound
  requests yet — add later if you want to use the tunnel routinely.
