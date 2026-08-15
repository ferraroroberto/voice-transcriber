"""Generate / rotate the webapp bearer token.

Why this exists
---------------
The webapp's Cloudflare tunnel puts the recorder on a public URL. Even
behind Cloudflare Access, the bearer token adds a second factor on the
API itself — a caller past the Access policy still needs the token to
record / transcribe / polish on your hardware. This script writes a
strong token into `config/webapp_config.json` so you don't pick a weak
one.

Behaviour
---------
- With no `auth_token` set (the default): the gate is **off**. The
  webapp behaves exactly as before. Every caller reaches the API
  freely.
- After running this script: the gate is **on**. Loopback callers
  (the tk window, local probes, the tray's own usage) still bypass —
  remote (tunnel) callers must present the token.

How the phone gets the token
----------------------------
You don't type it. The tray bakes it into the URL automatically:

    Tray → 📋 Copy Cloudflare URL  appends `?token=…` to the URL it
    copies. Paste into the phone's browser, open once.

The page extracts the token, stores it in localStorage, and strips
it from the visible URL (so your Home Screen icon stays clean). All
later visits authenticate from localStorage — nothing to type.

Rotation = re-run with --force, then re-open the new tokenised URL
once on each device that should keep working. Old devices stop working
immediately, which is the point.

Usage
-----
    python scripts/gen_token.py            # generate iff none set
    python scripts/gen_token.py --force    # rotate even if one exists
    python scripts/gen_token.py --clear    # disable the gate
"""

from __future__ import annotations

# Standard library imports
import argparse
import logging
import secrets
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Make sure emoji in log output survive a cp1252 PowerShell.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _utf8 import reconfigure_utf8_streams  # noqa: E402  — sys.path tweak above

reconfigure_utf8_streams()

# Local imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.webapp_config import (  # noqa: E402  — sys.path tweak above
    DEFAULT_CONFIG_PATH,
    load_webapp_config,
    save_webapp_config,
)


def _generate() -> str:
    return secrets.token_urlsafe(32)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing auth_token",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="clear auth_token (disables the auth gate)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_webapp_config()
    if args.clear:
        cfg.auth_token = ""
        save_webapp_config(cfg)
        log.info("🧹 Cleared auth_token in %s", DEFAULT_CONFIG_PATH)
        log.info("   The webapp's auth gate is now OFF.")
        return 0

    if cfg.auth_token and not args.force:
        log.info(
            "ℹ️  auth_token is already set in %s.\n"
            "   Re-run with --force to rotate, or --clear to disable.",
            DEFAULT_CONFIG_PATH,
        )
        return 0

    token = _generate()
    cfg.auth_token = token
    save_webapp_config(cfg)

    log.info("")
    log.info("✅ Wrote a new auth_token to:")
    log.info("   %s", DEFAULT_CONFIG_PATH)
    log.info("")
    log.info("Token (also saved above — no need to copy):")
    log.info("   %s", token)
    log.info("")
    log.info("What happens next")
    log.info("─────────────────")
    log.info("• The webapp's auth gate is now ON. Restart the tray or the")
    log.info("  webapp process so the new config is picked up.")
    log.info("• Loopback callers (the tk main window) keep working without")
    log.info("  the token — local UX is unchanged.")
    log.info("• Remote (tunnel) callers must present the token. They pick")
    log.info("  it up automatically the first time they open a tokenised URL:")
    log.info("    – Tray menu → 📋 Copy Cloudflare URL (URL already includes")
    log.info("      ?token=…). Paste into the phone's browser, open it,")
    log.info("      done. The page strips ?token=… from the visible URL")
    log.info("      after stashing it in localStorage.")
    log.info("• Rotation: re-run with --force, then re-open the new")
    log.info("  tokenised URL once on each device that should keep")
    log.info("  working. Other devices stop working immediately.")
    log.info("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
