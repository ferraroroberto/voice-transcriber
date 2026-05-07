# TODO — spinoffs

Living list of follow-ups outside the scope of the 2026-05-07 webapp
work. Add to it whenever a clean spinoff comes up; cross items off as
they ship. Not a changelog — for execution history see the dated docs
in this folder.

---

## 1. Cloudflare tunnel: enforce bearer-token auth

**Why.** `webapp_tunnel.bat` exposes the webapp on a public
`https://*.trycloudflare.com` URL. Right now anyone who guesses or
intercepts that URL can record, transcribe, and polish — burning your
local hardware and (for `claude-*` polish models) your Claude
subscription quota. Tunnels are short-lived but treat the URL as
sensitive while up. Tailscale-only / loopback paths are already gated
by Tailscale's ACL, so this only matters when the tunnel is the
exposure path.

**Scope.** A FastAPI dependency / middleware in
`app/webapp/server.py` that:

- Reads `cfg.auth_token` from `config/webapp_config.json` (the
  `auth_token` field already exists in the schema and the dataclass —
  see `src/webapp_config.py`).
- If empty: enforcement off (current behaviour).
- If non-empty: require `Authorization: Bearer <token>` on every API
  endpoint except `/healthz`, `/install-ca`, and the static mount.
  Loopback (`127.0.0.1`, `::1`) bypasses for the tk window's reuse of
  these endpoints.
- Tailnet bypass is optional — the simplest first cut is "loopback
  bypass only, token everywhere else" so the iPhone-over-Tailscale
  flow keeps working only when it carries the token.
- Frontend (`app/webapp/static/app.js`): read the token from a query
  param on first load (`?token=...`), persist to `localStorage`, attach
  to every `fetch` as `Authorization: Bearer <token>`. The Cloudflare
  flow would then be: launcher reads `last_tunnel_url.txt`, appends
  `?token=<value>` from `webapp_config.json`, opens that on the phone.

**Effort.** ~30 lines in `server.py`, ~15 in `app.js`, plus a tiny
`scripts/gen_token.py` helper that writes a `secrets.token_urlsafe(32)`
into `webapp_config.json` so the user doesn't pick a weak one.

**Reference.** The pattern is similar to how
`automation/launcher/launcher.py` gates with `LAUNCHER_PASSWORD` — a
simple session cookie + bearer combination. Don't reuse the cookie path
here; an opaque bearer is enough since the webapp has no concept of
"login session", just a short-lived public surface.

**Definition of done.**

- Tunnel running with `auth_token` set: requests without the header get
  401; requests with it work.
- Tunnel running with `auth_token` empty: behaves exactly as today.
- Tray menu's *Copy mobile URL* still works (loopback / Tailscale path
  unchanged).
- README troubleshooting row added explaining 401 vs 502 for someone
  who forgets to update the token on the phone after rotating it.

---

## 2. `automation/launcher` — detect uvicorn / FastAPI launchers + surface tunnel URL

**Repo.** `E:\automation\automation\launcher\` — the Flask-based
"Cloud Code / Apps" launcher tray.

**Why.** Today the **Apps** tab discovers projects by grepping bats for
`streamlit run`. `voice-transcriber` uses `uvicorn`, so the scan
doesn't find `webapp.bat` or `webapp_tunnel.bat`. Manual workaround
exists (edit `apps_config.json` by hand) but it's fragile.

Second pain point: when you're remote and start the tunnel via the
launcher, you can't see the public URL — it's printed in the
cloudflared console window on the home PC. We already write it to
`webapp/last_tunnel_url.txt`; just need a tap-to-reveal in the
launcher UI.

**Scope.**

- `launcher.py` scan: extend the regex/grep that picks up
  `streamlit run` to also match `uvicorn ` and `app/webapp/server`.
  Section title in the UI: *Web apps* (separate from *Streamlit apps*).
- New `📡 Show last tunnel URL` button next to each detected
  `*tunnel*.bat` entry. Reads
  `<project_dir>/webapp/last_tunnel_url.txt` (the path is the same
  layout convention voice-transcriber uses — could become a documented
  hook other repos opt into). If the file is missing or empty, button
  is disabled with tooltip "tunnel not running".
- Keep the existing port-busy `⛔ Kill :8501` red button generic — add
  a parallel `⛔ Kill :8443` for webapp processes.

**Effort.** Small — ~40 LOC across `launcher.py` and
`templates/apps.html`, plus minimal CSS for the new section. No backend
schema change to `apps_config.json` required (the `bat_path` is already
agnostic to what kind of server it spawns).

**Reference.** The grep is in `launcher.py` near
`scan_apps()` — find the `'streamlit run'` literal and add
`'uvicorn '` as a sibling. The Apps-tab template lives in
`templates/apps.html`.

**Definition of done.**

- Launcher's Apps tab on a fresh scan shows `webapp` and
  `webapp tunnel` (auto-named from the parent folder) under a *Web apps*
  subsection.
- Tapping `webapp tunnel` fires `webapp_tunnel.bat`. After ~5 s, the
  *📡 Show last tunnel URL* row reveals the captured URL as a clickable
  link. From the iPhone over Tailscale you can now bootstrap a
  Cloudflare exposure entirely from the launcher without seeing the PC
  console.
- Existing Streamlit launchers behave exactly as before.

---

## Process notes

- These are intentionally split across two repos — keep them that way.
  Tying voice-transcriber's commit history to launcher feature work
  bloats both.
- When picking up #1, also update
  `docs/2026-05-07-mobile-webapp-and-repo-cleanup.md` with a dated
  follow-up note, not a new design doc — same effort, same scope.
- When picking up #2, the launcher repo gets its own dated
  `docs/YYYY-MM-DD-uvicorn-app-detection.md`.
