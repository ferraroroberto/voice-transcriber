"""Set or clear the webapp's login password.

Why this exists
---------------
The bearer token (`scripts/gen_token.py`) gates every non-loopback API
call. Bootstrapping a fresh device used to mean opening a long
tokenised URL — fine on a desktop, painful on a phone, and broken on
iOS PWAs whose localStorage is partitioned from Safari's main jar.

A password gives the user a memorable secret to type instead. The
webapp shows a login overlay whenever an API call returns 401; on
correct password, the server hands the bearer token back, the page
stashes it in localStorage, and from then on the device behaves as
if it had pasted the tokenised URL.

The password lives in `config/webapp_config.json` (gitignored)
alongside the bearer token. Failed attempts are logged with the
client IP to `webapp/auth.log`.

Usage
-----
    python scripts/set_password.py <password>   # set or rotate
    python scripts/set_password.py --clear      # disable password gate
"""

from __future__ import annotations

# Standard library imports
import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Make sure emoji in log output survive a cp1252 PowerShell.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Local imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.webapp_config import (  # noqa: E402  — sys.path tweak above
    DEFAULT_CONFIG_PATH,
    load_webapp_config,
    save_webapp_config,
)

# Floor on what this script will accept. The webapp exposes the same
# value on a public hostname when the tunnel is up, so a value short
# enough to be enumerated is not a reasonable default to allow silently.
MIN_PASSWORD_LENGTH = 10


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "password",
        nargs="?",
        help="the new password to set",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="clear auth_password (disables the password prompt)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_webapp_config()

    if args.clear:
        cfg.auth_password = ""
        save_webapp_config(cfg)
        log.info("🧹 Cleared auth_password in %s", DEFAULT_CONFIG_PATH)
        log.info("   The password prompt is now OFF.")
        return 0

    if not args.password:
        parser.error("provide a password as the first argument, or use --clear")

    if not cfg.auth_token:
        log.error(
            "ℹ️  No auth_token is set yet — the password by itself does\n"
            "   nothing because /api/login hands back the bearer token.\n"
            "   Run `python scripts/gen_token.py` first, then re-run this."
        )
        return 1

    if len(args.password) < MIN_PASSWORD_LENGTH:
        log.error(
            "❌ Too short — this value is reachable from the public tunnel,\n"
            "   so it needs at least %d characters (got %d). A short\n"
            "   passphrase of a few words types easily on a phone and is\n"
            "   comfortably above the floor.",
            MIN_PASSWORD_LENGTH,
            len(args.password),
        )
        return 1

    cfg.auth_password = args.password
    save_webapp_config(cfg)
    log.info("✅ Set auth_password (length %d)", len(args.password))
    log.info("   Stored in: %s", DEFAULT_CONFIG_PATH)
    log.info("")
    log.info("Next steps")
    log.info("──────────")
    log.info("• Restart the tray so the new value is picked up.")
    log.info("• Open the webapp on a device with no token in localStorage")
    log.info("  (e.g. iPhone PWA). The login overlay appears — type the")
    log.info("  password, the server hands the bearer token back, the")
    log.info("  page stashes it, you're in.")
    log.info("• Failed attempts are logged with client IP to:")
    log.info("  %s", PROJECT_ROOT / "webapp" / "auth.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
