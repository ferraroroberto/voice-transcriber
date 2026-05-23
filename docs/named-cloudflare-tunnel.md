# 2026-05-09 — Named Cloudflare tunnel for a persistent public URL

## What was done

The existing `webapp_tunnel.bat` produces a fresh
`*.trycloudflare.com` URL on every launch — fine for ad-hoc remote
access but a daily friction-tax for a work PC that wants a stable
bookmark. Added a parallel **named tunnel** path tied to the user's
own subdomain (e.g. `voice.robertoferraro.net`) so the URL never
changes. Free if you already own a domain on Cloudflare.

The two flows now coexist: pick the one that fits the moment.

| Path | URL | Survives restart? |
|---|---|---|
| `webapp_tunnel.bat` (existing) | `https://*.trycloudflare.com` | No |
| `webapp_tunnel_named.bat` (new) | `https://voice.<your-domain>` | Yes |

## Files added

- `scripts/run_named_tunnel.py` — orchestrator. Mirrors
  `run_tunnel.py` (uvicorn boot-or-adopt, Ctrl+C cleanup, output
  streaming) but invokes `cloudflared tunnel --config <path> run`
  against a pre-created tunnel instead of `--url`. Reads the
  hostname from the YAML's first `ingress[]` entry and writes
  `https://<hostname>` (with `?token=…` if `auth_token` is set) to
  `webapp/last_tunnel_url.txt` so the tray's existing "📋 Copy
  mobile URL" path keeps working unchanged.
- `webapp_tunnel_named.bat` — user-facing entry point. Validates
  `.venv`, `cloudflared` on PATH, and `webapp/cloudflared.yml`
  presence before launching the orchestrator.
- `webapp/cloudflared.sample.yml` — committed template with
  placeholders + setup notes inline.
- `webapp/cloudflared.yml` — gitignored, holds the user's actual
  tunnel UUID + hostname. Pattern matches the existing
  `webapp_config.json` / `webapp_config.sample.json` split.
- `docs/named-cloudflare-tunnel.md` — this entry.

## Files modified

- `.gitignore` — exclude `webapp/cloudflared.yml`.
- `README.md`:
  - "Modes of use" table gains a `webapp_tunnel_named.bat` row.
  - "Where to launch from" table gains a "persistent URL" scenario.
  - New section **"Persistent URL via named Cloudflare tunnel"**
    walks through the four `cloudflared` commands, the YAML config,
    and the recommended Cloudflare Access policy (free Zero Trust
    plan, Google sign-in restricted to the owner's email). Three
    layers in front of the webapp: domain obscurity → Access
    sign-in → bearer token.
  - Troubleshooting table gains three rows for named-tunnel
    failure modes (missing config, missing credentials JSON,
    Access blocking the owner).

## How it differs from the quick-tunnel script

`run_tunnel.py` watches cloudflared's stdout for the random
`*.trycloudflare.com` URL and persists whatever it finds.
`run_named_tunnel.py` skips that — the URL is fixed, so it's
written from the YAML's hostname before cloudflared even starts.
Everything else (uvicorn lifecycle, signal handling,
`last_tunnel_url.txt` cleanup on shutdown) is intentionally
identical so the two `.bat` entry points feel like twins to the
user.

## Validation

- `python -m py_compile scripts/run_named_tunnel.py` clean.
- Sample YAML is valid (uses placeholder UUID
  `00000000-0000-0000-0000-000000000000` so `tunnel run` would
  refuse to start with it — that's the safety property).
- `webapp/cloudflared.yml` parses; hostname extraction returns
  `voice.robertoferraro.net`.

## Out of scope (deliberate)

- No automated Cloudflare Access policy provisioning — manual via
  Zero Trust dashboard. Touching the user's Cloudflare account
  programmatically would need an API token with significant
  privileges and is overkill for a one-time setup.
- No tray menu integration for the named tunnel yet. The existing
  "📋 Copy mobile URL" tray entry copies the local URL; the
  persistent URL is bookmarked in the user's browser. If we ever
  want to surface the tunnel URL in the tray, the data is already
  in `webapp/last_tunnel_url.txt`.
