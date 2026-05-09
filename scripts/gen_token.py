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
import secrets
import sys
from pathlib import Path

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

    cfg = load_webapp_config()
    if args.clear:
        cfg.auth_token = ""
        save_webapp_config(cfg)
        print(f"🧹 Cleared auth_token in {DEFAULT_CONFIG_PATH}")
        print("   The webapp's auth gate is now OFF.")
        return 0

    if cfg.auth_token and not args.force:
        print(
            f"ℹ️  auth_token is already set in {DEFAULT_CONFIG_PATH}.\n"
            f"   Re-run with --force to rotate, or --clear to disable."
        )
        return 0

    token = _generate()
    cfg.auth_token = token
    save_webapp_config(cfg)

    print()
    print("✅ Wrote a new auth_token to:")
    print(f"   {DEFAULT_CONFIG_PATH}")
    print()
    print("Token (also saved above — no need to copy):")
    print(f"   {token}")
    print()
    print("What happens next")
    print("─────────────────")
    print("• The webapp's auth gate is now ON. Restart the tray or the")
    print("  webapp process so the new config is picked up.")
    print("• Loopback callers (the tk main window) keep working without")
    print("  the token — local UX is unchanged.")
    print("• Remote (tunnel) callers must present the token. They pick")
    print("  it up automatically the first time they open a tokenised URL:")
    print("    – Tray menu → 📋 Copy Cloudflare URL (URL already includes")
    print("      ?token=…). Paste into the phone's browser, open it,")
    print("      done. The page strips ?token=… from the visible URL")
    print("      after stashing it in localStorage.")
    print("• Rotation: re-run with --force, then re-open the new")
    print("  tokenised URL once on each device that should keep")
    print("  working. Other devices stop working immediately.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
