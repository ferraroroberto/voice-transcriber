"""Build identity + static-asset versioning for the webapp.

Lets the mobile webapp prove which build it is running, so "did the
deploy take, or is the iPhone serving stale cached code?" stops being
answered by feel:

  * content-hash query stamps on ``app.js`` / ``styles.css`` so any edit
    changes the URL — no manual ``?v=N`` bumps, no stale iOS cache,
  * a build identity (git SHA + build time) surfaced via ``/api/version``
    and a glanceable line in the Settings panel.

Every value is computed once when :class:`BuildInfo` is constructed at
webapp startup — the tray restarts on every code edit per project
convention, so there is no watcher and no per-request work.
"""

from __future__ import annotations

# Standard library imports
import hashlib
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

# Assets that carry a manual ``?v=`` stamp in index.html today and are
# content-hash stamped instead. The placeholder in index.html for each
# is the uppercased name with dots turned to underscores, e.g.
# ``app.js`` -> ``__APP_JS__``.
STAMPED_ASSETS = ("app.js", "styles.css")


def _placeholder(asset_name: str) -> str:
    """The index.html token a content hash replaces, e.g. ``__APP_JS__``."""
    return "__" + asset_name.replace(".", "_").upper() + "__"


def asset_hash(path: Path) -> str:
    """Return the first 8 hex chars of the file's SHA-256.

    Falls back to ``"missing"`` when the file can't be read so a partial
    deployment degrades to a stable (if uninformative) stamp instead of
    crashing the page.
    """
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        logger.warning(f"⚠️  Could not hash {path} ({exc})")
        return "missing"
    return digest[:8]


def _git_short_sha(repo_root: Path) -> str:
    """Short git SHA of ``HEAD``.

    Returns ``"unknown"`` when git isn't available — e.g. the project was
    deployed from a tarball rather than a clone.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"⚠️  git SHA unavailable ({exc})")
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


class BuildInfo:
    """Immutable build identity, computed once at webapp startup."""

    def __init__(self, static_dir: Path, repo_root: Path) -> None:
        self.asset_hashes: Dict[str, str] = {
            name: asset_hash(static_dir / name) for name in STAMPED_ASSETS
        }
        self.git_sha: str = _git_short_sha(repo_root)
        self.built_at: str = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )

    def stamp_html(self, html: str) -> str:
        """Replace the ``?v=__NAME__`` placeholders in index.html with the
        content hash of each stamped asset."""
        for name, digest in self.asset_hashes.items():
            html = html.replace(_placeholder(name), digest)
        return html

    def as_dict(self) -> Dict[str, str]:
        """Payload for the ``/api/version`` endpoint."""
        return {
            "git_sha": self.git_sha,
            "built_at": self.built_at,
            "asset_hash": self.asset_hashes.get("app.js", "missing"),
        }
