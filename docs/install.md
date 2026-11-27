# Install, update and migrate Voice Transcriber

Voice Transcriber installs, updates and migrates the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. A migration, in the same folder or onto a fresh clone, is one `setup.bat` call. None of them ever resets or overwrites what a PC has configured. The generic installer and its `[install]` tables are described in project-scaffolding's `docs/installer.md`; this app's declarations are in `.fleet.toml`.

## Install

You need:

- Windows 10 or 11, `git`, and Python 3.11 or newer (the `py` launcher, or `python` on `PATH`).
- A microphone, and about 2 GB of free disk for `vendor/` (the whisper.cpp release and the `ggml-large-v3-turbo.bin` model).
- Optional: an NVIDIA GPU (the prebuilt CUDA server is chosen automatically, else the CPU build), `cloudflared` for remote access ([remote access](remote-access.md)), and the `local-llm-hub` on `:8000` for the primary transcription path (the app falls back to its own whisper-server when the hub is down).

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

### Uninstall

1. Turn off the log-on autostart: delete the `tray.bat` shortcut from `shell:startup`.
2. Remove what the app registered outside its folder: the CA it installed in the Windows user trust store (`certmgr.msc`, Trusted Root Certification Authorities, "Voice Transcriber Local CA") and, if you used the tunnel, the Cloudflare tunnel and its DNS record (`cloudflared tunnel delete`).
3. Stop the app: right-click the tray icon and choose Quit. This also stops the whisper-server it started; a whisper-server on `:8090` that belongs to `local-llm-hub` is not this app's to stop.
4. Delete the folder. Keep a copy of the files listed under [Migrate](#migrate) first if you may want them back. The runtime databases in `C:\sqlite\voice-transcriber\` stay behind; delete that folder too when you no longer want the activity and usage logs.

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

## Migrate

What moves is declared in `[install.migrate]` of `.fleet.toml`, each file only when the old copy has it:

- configs: `config/webapp_config.json`, `config/vocabulary.json`, `config/snippets.json`, `config/speaker_blocklist.json`, `config/disfluencies.json`;
- sign-in password: `config/app_password.json` (the salted hash);
- state: `archive/`, the saved takes with their audio, transcripts and history;
- certificates and tunnel: `webapp/certificates/`, `app/webapp/static/ca.crt` and `app/webapp/static/voice-transcriber-ca.mobileconfig` (together they keep the CA that your phone already trusts valid), and `webapp/cloudflared.yml`.

The runtime databases (`activity.sqlite3`, `usage.sqlite3`) are not in the folder. They live in the `voice-transcriber` folder of the runtime-data root (`C:\sqlite\voice-transcriber\` by default), where the new copy reads them. `vendor/` is not copied: setup downloads it again, so the PC needs a connection and about 2 GB for it. `webapp/last_tunnel_url.txt` and `webapp/auth.log` are rewritten by the app. The installer does the whole move; nothing here or in `setup.bat` deletes the old copy.

**Reinstall in the same folder (the usual case).** From a plain terminal whose current folder is outside the app, after `git pull --ff-only` in the app so it has the current `setup.bat`:

```powershell
cd <PARENT FOLDER>
.\voice-transcriber\setup.bat -InPlace -WhatIf    # read the backup name and every step; nothing changes
.\voice-transcriber\setup.bat -InPlace
```

It stops the app, keeps the replaced copy as `voice-transcriber.bak-YYYYMMDD-N`, clones this folder's `origin` into the same path and passes only when the new clone serves its own commit on `/api/version`. The path is unchanged, so the Startup shortcut and the launcher's app entry need no edit. `-WhatIf` lists every file it would copy, one line each, so a large `archive/` makes it long. The archive is copied, not moved, so it needs that much free disk again, and the migration step (copy, install, downloads, start) is cut off after 30 minutes.

**Beside it (another folder, or an old copy that is not a git clone):**

```powershell
git clone https://github.com/ferraroroberto/voice-transcriber.git <NEW FOLDER>
cd <NEW FOLDER>
.\setup.bat -MigrateFrom <OLD FOLDER> -StopOld
```

`-StopOld` is needed on the same PC: two copies cannot both run there (one tray lock, one port). To skip the download, copy the old copy's `vendor/` folder into the new clone before running it.

Only you can do these steps:

- **Re-apply your edits to tracked files**, if you made any: `config/config.json` (for example `machine_specific_mics`, the hotkey, `webapp.port`), `config/polish_prompts.json`, `config/whisper_server.yaml`. The installer never copies a tracked file; `git diff` in the old copy shows them. Re-apply only the lines you changed, then restart the app.
- **Move what Git keeps locally**, if you work in this clone: local branches, stashes and ignored files not listed above stay in the backup, and a linked worktree stops working once its main folder is renamed. Finish or remove worktrees before you start.
- **On another PC**, copy the old folder over first and use `-MigrateFrom`. Copy the runtime-data folder too, or start with empty logs. The tunnel's credentials file lives in `%USERPROFILE%\.cloudflared\`, outside the app: run `cloudflared tunnel login` and copy that file by hand.
- **Repoint** whatever stores the old path, after a `-MigrateFrom` into another folder (the Startup shortcut to `tray.bat`, the launcher's app entry): the run lists what still points at it.
- **Check it:** the History tab lists the copied takes, and one real take records and transcribes.
- **Fall back** if verify fails (the report names the step): stop the new copy, rename the new folder away and the backup to the original name, and start it; it is untouched.
- **Retire the backup** once you are sure the new copy works, by deleting it yourself. An agent working through this playbook asks you before it deletes anything.
