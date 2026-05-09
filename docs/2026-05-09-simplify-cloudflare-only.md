# 2026-05-09 — Simplify to Cloudflare-only remote access

## What was done

The remote-access surface was three overlapping paths: Tailscale
(direct LAN-style access via tailnet IP), Cloudflare quick tunnel
(`webapp_tunnel.bat`, ephemeral `*.trycloudflare.com` URL), and
Cloudflare named tunnel (`webapp_tunnel_named.bat`, persistent URL on
the user's own domain). With the named tunnel + Cloudflare Access
proven to work, the other two were carrying maintenance cost without
unique value. Simplified to one path: **named Cloudflare tunnel,
auto-spawned by the tray**.

## Files removed

- `webapp_tunnel.bat`
- `scripts/run_tunnel.py`

## Files modified

- `app/gui/tray.py`:
  - Tray now spawns `cloudflared tunnel --config webapp/cloudflared.yml run`
    at boot when the YAML exists, alongside whisper + uvicorn. Logs
    a warning and shows a toast if `cloudflared` is missing from
    `PATH` or fails to launch — never blocks the rest of the tray.
  - Stops the cloudflared subprocess in `_shutdown()` before the
    webapp so the public URL goes 5xx immediately during teardown.
  - Hostname is read from the YAML's first `ingress[].hostname`
    via a small `_read_tunnel_hostname()` helper.
  - Menu replaces the single **📋 Copy mobile URL** with two items:
    - **📋 Copy local URL** → `https://127.0.0.1:8443` (loopback,
      bypasses the auth gate but token is still appended for use
      from local tools).
    - **📋 Copy Cloudflare URL** → `https://<hostname>` (the
      persistent URL with `?token=…` when the bearer token is set).
      Greyed out automatically when no `cloudflared.yml` exists.
- `README.md`:
  - "Modes of use" + repo layout drop `webapp_tunnel.bat`.
  - "Where to launch from" table collapses three remote rows into
    one ("Anywhere else → Cloudflare URL").
  - "First-time setup" drops the iOS CA-trust + microphone-trust
    sections — Cloudflare terminates TLS at the edge so the phone
    sees a valid public cert and never needs to trust the local CA.
    Local cert generation stays for the loopback browser path.
  - "Optional: bearer-token auth" rewritten around the new
    "📋 Copy Cloudflare URL" tray entry instead of "📋 Copy mobile
    URL" / `last_tunnel_url.txt`.
  - "Persistent URL via named Cloudflare tunnel" → renamed to
    "Persistent URL via Cloudflare tunnel" (no longer a
    contrast with "quick"). Daily-use section says `tray.bat`,
    not `webapp_tunnel_named.bat`.
  - Troubleshooting table dropped Tailscale rows; added rows for
    `ERR_NAME_NOT_RESOLVED` (DNS not propagated / not on
    Cloudflare), token-after-rotation, Access lockout,
    cloudflared-missing-from-path, and missing `cloudflared.yml`.
- `scripts/gen_token.py` — docstring + post-run instructions
  rewritten around "📋 Copy Cloudflare URL"; dropped quick-tunnel
  / Tailscale references.
- `scripts/run_named_tunnel.py` — docstring positions the script
  as the "headless / no-tray" path now that the tray covers the
  normal case.
- `scripts/gen_ssl_cert.py` — docstring trimmed to reflect that
  the cert is for the loopback path only; Cloudflare provides
  public TLS for everything else. The auto-detection of LAN /
  hostname / Tailscale interfaces is left as-is — it falls back
  gracefully if Tailscale isn't installed.

## What stays (deliberately)

- `scripts/run_named_tunnel.py` + `webapp_tunnel_named.bat` —
  useful for headless boxes where no tray runs. Drop later if
  unused.
- `webapp/certificates/` and `gen_ssl_cert.py` — cloudflared
  expects `https://localhost:8443` as its origin (`noTLSVerify`
  is on, but the origin must still serve TLS). Local browser use
  also benefits from the green padlock.
- `/install-ca` endpoint on the webapp — harmless, still serves
  the iOS profile if anyone hits it.
- `last_tunnel_url.txt` writes from `run_named_tunnel.py` —
  useful for external tools that want to read the tokenised URL.

## Validation

- `python -m py_compile` clean across `tray.py`, `gen_token.py`,
  `run_named_tunnel.py`, `gen_ssl_cert.py`.
- `_read_tunnel_hostname(TUNNEL_CONFIG_PATH)` returns
  `voice.robertoferraro.net` against the live config.
- README grep for `Tailscale|tailnet|ts\.net|trycloudflare|webapp_tunnel\.bat`
  returns zero matches.
