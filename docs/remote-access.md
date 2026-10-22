# Remote access: token, password, tunnel

## Bearer-token auth (required for the tunnel)

The webapp ships with the auth gate **off** by default — `auth_token`
is `""` in `config/webapp_config.json`. That's fine for loopback-only
use: the tk window and local probes reach the API freely either way,
zero config required. But the Cloudflare tunnel will not
start at all while `auth_token` is empty (`src/tunnel.py`
`publish_refusal_reason` refuses the spawn) — so the token is a
**prerequisite for the persistent URL**, not an optional extra layer.
Pair it with Cloudflare Access in front of your tunnel for a second
factor — even a caller past the Access policy still needs the token.
Turn it on:

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

### Password gate (companion to the token)

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

## Persistent URL via Cloudflare tunnel

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

### One-time setup

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

# 6. Generate the bearer token — the tunnel refuses to start without
#    one (see "Bearer-token auth" below)
& .\.venv\Scripts\python.exe scripts\gen_token.py
```

`webapp/cloudflared.yml` is gitignored so your tunnel UUID + hostname
don't end up in the repo. Default `credentials-file` lookup at
`~/.cloudflared/<UUID>.json` works out of the box; only set it
explicitly in the YAML if you stored the credentials JSON somewhere
else. Step 6 isn't optional: cloudflared won't spawn at all
while `auth_token` is empty.

### Cloudflare Access policy (recommended)

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

### Daily use

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

