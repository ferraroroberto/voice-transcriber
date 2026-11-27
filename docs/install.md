# Install, update and move Voice Transcriber

Voice Transcriber moves to another Windows PC the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. Neither ever resets or overwrites what a PC has configured. The generic installer and its `[install]` table are described in project-scaffolding's `docs/installer.md`; this app's declarations are in `.fleet.toml`.

## What you need

- Windows 10 or 11, `git`, and Python 3.11 or newer (the `py` launcher, or `python` on `PATH`).
- A microphone, and about 2 GB of free disk for `vendor/` (the whisper.cpp release and the `ggml-large-v3-turbo.bin` model).
- Optional: an NVIDIA GPU (the prebuilt CUDA server is chosen automatically, else the CPU build), `cloudflared` for remote access ([remote access](remote-access.md)), and the `local-llm-hub` on `:8000` for the primary transcription path (the app falls back to its own whisper-server when the hub is down).

## Fresh PC

```powershell
git clone https://github.com/ferraroroberto/voice-transcriber.git
cd voice-transcriber
.\setup.bat
.\tray.bat
```

`setup.bat` runs `src/installer.py`, which:

1. creates `.venv` when it is missing;
2. installs `requirements.txt`, and later reinstalls only when that file changed (its hash is kept inside `.venv`);
3. runs `scripts/setup_assets.py`, which fetches the whisper.cpp server and the ggml model into `vendor/whisper.cpp/`; it reruns only when that script or `config/whisper_server.yaml` changed, and each fetch returns at once when its file is already there;
4. creates each ignored config from its tracked sample when the config is missing (`config/webapp_config.json`, `vocabulary.json`, `snippets.json`, `speaker_blocklist.json`, `disfluencies.json`);
5. ends with two lists: the required items, each `ok`, `missing` or `unknown`, and the optional features, each `available`, `off` or `unknown` with what it unlocks.

Setup has succeeded when it ends with `Setup complete` and exits 0; an optional feature that is `off` never changes that. The optional features, each needed only for what it unlocks:

- **Sign-in password:** `.\.venv\Scripts\python.exe -m src.password set`. Only needed when the webapp is reachable from other devices ([remote access](remote-access.md)). The item reads the stored hash: `available` when a usable one (or the legacy `auth_password`) is there, `off` when none is, an empty or corrupt `config/app_password.json` included, and `unknown` when a file it needs cannot be read.
- **HTTPS certificate:** `.\.venv\Scripts\python.exe scripts\gen_ssl_cert.py`. It writes `webapp/certificates/` and installs the CA into the Windows user trust store; restart the tray afterwards. A phone that trusted the old CA has to install the new profile again ([iPhone debugging](iphone-debugging.md)).
- **whisper.cpp server / ggml model:** `setup.bat` fetches both; if the checklist still says `off`, the fetch failed (read its output above the checklist), and `.\.venv\Scripts\python.exe scripts\setup_assets.py` retries it. When the PC cannot download it and already has a working copy, `.\.venv\Scripts\python.exe scripts\install_whisper_cpp.py --from <OTHER FOLDER>\vendor\whisper.cpp` copies that copy (and its models) in instead ([GPU / CPU compatibility](configuration.md#-gpu--cpu-compatibility)).

Remote access is not part of `setup.bat`: it needs your Cloudflare account. Follow [Persistent URL via Cloudflare tunnel](remote-access.md#persistent-url-via-cloudflare-tunnel) to create `webapp/cloudflared.yml`; without that file the tray simply skips the tunnel.

To start at log on, put a shortcut to `tray.bat` in the Startup folder (`shell:startup`). The installer has no autostart step for this app, so `setup.bat -Startup` is not used.

`.\setup.bat -WhatIf` lists every change it would make and makes none.

## Update

```powershell
git pull --ff-only
.\setup.bat
.\.venv\Scripts\python.exe -m src.restart_check restart --port 8443
```

The restart command exits non-zero unless a different process now serves `HEAD`; confirm with `GET http://127.0.0.1:8443/api/version` (`git_sha` matches `HEAD`). It leaves `:8090` and `:8091` (the shared whisper and translate servers) alone.

`git pull` always fast-forwards: the app never rewrites a tracked file. `config/config.json`, `config/polish_prompts.json` and `config/whisper_server.yaml` are tracked and only read; everything the app writes (`config/webapp_config.json`, `config/app_password.json`, `archive/`, `webapp/`, the runtime database folder) is ignored. A tracked file you edited by hand is the one thing that can stop a pull: stash or revert it, pull, and re-apply the edit.

`setup.bat` on a configured PC is safe, and it is how a new setting arrives:

- `config/webapp_config.json` is the one settings file: an existing value is never changed. A key the sample has and the file lacks is added, after a timestamped backup beside it (`webapp_config.json.bak-<YYYYmmdd-HHMMSS>`), and the added key names are printed, never values. A file that does not parse is left alone and reported, and the run exits 1.
- `config/vocabulary.json`, `snippets.json`, `speaker_blocklist.json` and `disfluencies.json` are your own lists: created when missing, never rewritten or reformatted.
- A second run reports `unchanged` and writes nothing.

## Migrate from an existing install

Use this on a PC that already runs an older copy of Voice Transcriber: one that may not be a git clone, and may live at another path. The old copy keeps running until the new one is verified, and it is deleted only when you say so.

1. **Clone fresh beside it.** `git clone https://github.com/ferraroroberto/voice-transcriber.git <NEW FOLDER>`; never clone over the old folder.
2. **Copy this PC's own files across.** From the old folder into the new one, copy only these, keeping their relative paths. All of them are gitignored, so nothing tracked is touched:
   - configs: `config/webapp_config.json` (the bearer token, polish defaults, retention, every setting you changed in the app; it may hold secrets, so copy the file, never paste its content anywhere), `config/vocabulary.json`, `config/snippets.json`, `config/speaker_blocklist.json`, `config/disfluencies.json` (each only if the old copy has it);
   - sign-in password: `config/app_password.json` (the salted hash);
   - state: `archive/`, the saved takes with their audio, transcripts and history. Skip it to start with an empty history;
   - certificates and tunnel: `webapp/certificates/`, `app/webapp/static/ca.crt` and `app/webapp/static/voice-transcriber-ca.mobileconfig` (together they keep the CA that your phone already trusts valid), and `webapp/cloudflared.yml` when the tunnel is used. `webapp/last_tunnel_url.txt` and `webapp/auth.log` are rewritten by the app and need no copy. The tunnel's credentials file lives in `%USERPROFILE%\.cloudflared\`, outside the folder: it stays put on the same PC, and a different PC needs `cloudflared tunnel login` and the credentials file copied by hand;
   - optional, to skip a 1.6 GB download: `vendor/` (the whisper.cpp release and the model).
   - Tracked files you edited in the old copy, if you did: `config/config.json` (for example `machine_specific_mics`, the hotkey), `config/polish_prompts.json`, `config/whisper_server.yaml`. In a git clone `git diff` in the old folder shows them; otherwise compare against the new files. Re-apply only the lines you changed instead of copying whole files over, so a later `git pull --ff-only` still has nothing to refuse.
   - The runtime databases (`activity.sqlite3`, `usage.sqlite3`) are not in the folder: they live in the fleet runtime-data root, `C:\sqlite\voice-transcriber\` by default. On the same PC the new copy reads them where they are and there is nothing to copy; on another PC copy that folder across, or leave it to start with an empty log.
3. **Top them up.** Run `.\setup.bat -WhatIf` in the new folder and read what it would change, then `.\setup.bat`. `webapp_config.json` gains only the keys it lacks; the values you copied stay as they were.
4. **Stop the old copy.** Right-click its tray icon and choose Quit, and remove or disable the old copy's `tray.bat` shortcut in `shell:startup`. Do this before starting the new one: two copies on `:8443` conflict, and two connectors on one Cloudflare tunnel split its traffic between them.
5. **Start the new one.** `.\tray.bat`, and put a shortcut to it in `shell:startup` if it should start at log on.
6. **Verify it.** Open `https://127.0.0.1:8443`, confirm `GET /api/version` shows the new commit, check the History tab lists the copied takes, then record one real take. If anything is wrong, quit the new tray and start the old copy again: it is untouched.
7. **Retire the old folder, only after you confirm.** Nothing in this playbook or in `setup.bat` deletes the old copy. When you are satisfied, rename it first (for example `<OLD FOLDER>.retired`) and run on the new copy for a while; delete it yourself once you are sure. An agent working through this playbook asks you before it deletes anything.

## Uninstall

1. Turn off the log-on autostart: delete the `tray.bat` shortcut from `shell:startup`.
2. Remove what the app registered outside its folder: the CA it installed in the Windows user trust store (`certmgr.msc`, Trusted Root Certification Authorities, "Voice Transcriber Local CA") and, if you used the tunnel, the Cloudflare tunnel and its DNS record (`cloudflared tunnel delete`).
3. Stop the app: right-click the tray icon and choose Quit. This also stops the whisper-server it started; a whisper-server on `:8090` that belongs to `local-llm-hub` is not this app's to stop.
4. Delete the folder. Keep a copy of the configs, state and certificates listed under "Migrate" first if you may want them back. The runtime databases in `C:\sqlite\voice-transcriber\` stay behind; delete that folder too when you no longer want the activity and usage logs.
