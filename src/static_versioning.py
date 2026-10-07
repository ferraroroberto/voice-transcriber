"""Build identity + static-asset versioning for the webapp.

Lets the mobile webapp prove which build it is running, so "did the
deploy take, or is the iPhone serving stale cached code?" stops being
answered by feel:

  * content-hash query stamps on every ``.js`` / ``.css`` asset so any
    edit changes the URL — no manual ``?v=N`` bumps, no stale iOS cache.
    A ``.js`` stamp hashes the module plus everything it imports, so an
    edit to a nested module also moves the URL of every importer above it,
    up to ``app.js`` (issue #220),
    ``index.html`` carries a ``?v=__NAME__`` placeholder for the two
    assets it references directly (``app.js`` + ``styles.css``); every
    other module is a transitive ``import`` from ``app.js`` and gets its
    ``?v=`` stamped by :meth:`BuildInfo.rewrite_js_imports` at serve time,
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
import posixpath
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

# Suffixes hashed + content-stamped. Everything else under static/
# (icons, manifest, mobileconfig) is cached more conservatively by the
# static mount itself.
STAMPED_SUFFIXES = (".js", ".css")

# Assets that ``index.html`` references directly via a ``?v=__NAME__``
# placeholder. The placeholder for each is the uppercased path with dots,
# hyphens and slashes turned to underscores, e.g. ``app.js`` -> ``__APP_JS__``,
# ``_vendored/nav/nav-tabs.css`` -> ``__VENDORED_NAV_NAV_TABS_CSS__``. Every
# other ``.js`` module is reached only through an ``import`` inside ``app.js``.
HTML_STAMPED_ASSETS = (
    "app.js",
    "styles.css",
    "_vendored/base/base.css",
    "_vendored/nav/nav-tabs.css",
    "_vendored/card/card.css",
    "_vendored/home-head/home-head.css",
    "_vendored/toast/toast.css",
    "_vendored/text-size/text-size.css",
    "_vendored/range-tab/range-tab.css",
    "_vendored/switch/switch.css",
    "_vendored/select-native/select-native.css",
    "_vendored/modal/modal.css",
    "_vendored/empty-state/empty-state.css",
    "_vendored/button/button.css",
    "_vendored/icon-button/icon-button.css",
)

# Static ES-module imports inside the JS graph: ``from './x.js'`` and the
# bare ``import './x.js'`` side-effect form — including subdir paths like
# ``./_vendored/nav/nav-tabs.js`` and parent-relative ones like the vendored
# ``../icons/icons.js``. The optional ``?v=`` group makes re-stamping an
# already-stamped body idempotent.
_JS_IMPORT_RE = re.compile(
    r"""(from\s*['"]|import\s*['"])(\.\.?/[\w\-./]+\.js)(\?v=[^'"]*)?(['"])"""
)


def _placeholder(asset_name: str) -> str:
    """The index.html token a content hash replaces, e.g. ``__APP_JS__``."""
    return "__" + re.sub(r"[./-]", "_", asset_name).upper().lstrip("_") + "__"


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
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"⚠️  git SHA unavailable ({exc})")
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _js_imports(static_dir: Path, name: str, known: Dict[str, str]) -> list[str]:
    """Static-relative keys of the modules ``name`` imports.

    Only imports that resolve to a hashed file count — the same rule
    :meth:`BuildInfo.rewrite_js_imports` applies when it stamps them.
    """
    try:
        body = (static_dir / name).read_text(encoding="utf-8")
    except OSError:
        return []
    base = posixpath.dirname(name)
    keys = (
        posixpath.normpath(posixpath.join(base, match.group(2)))
        for match in _JS_IMPORT_RE.finditer(body)
    )
    return [key for key in keys if key in known]


def _graph_hash(name: str, content_hashes: Dict[str, str], static_dir: Path) -> str:
    """Hash of ``name`` plus every module it transitively imports.

    Walks the import graph with a visited set, so an import cycle terminates.
    """
    seen = {name}
    pending = [name]
    while pending:
        for key in _js_imports(static_dir, pending.pop(), content_hashes):
            if key not in seen:
                seen.add(key)
                pending.append(key)
    digest = hashlib.sha256()
    for key in sorted(seen):
        digest.update(f"{key}={content_hashes[key]}\0".encode("utf-8"))
    return digest.hexdigest()[:8]


class BuildInfo:
    """Immutable build identity, computed once at webapp startup."""

    def __init__(self, static_dir: Path, repo_root: Path) -> None:
        # Hash every .js/.css file in static/ (recursively — the vendored
        # components live under static/_vendored/) — the whole ES-module
        # graph, not just the assets index.html names directly. Keys are
        # static-root-relative posix paths; for root-level files that is
        # just the filename, so old keys (``app.js``) are unchanged.
        content_hashes: Dict[str, str] = {
            path.relative_to(static_dir).as_posix(): asset_hash(path)
            for path in sorted(static_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in STAMPED_SUFFIXES
        }
        # A ``.js`` stamp covers the module's whole import graph, not just its
        # own bytes: modules are served immutably, so an importer whose URL
        # didn't move would keep naming the old stamp of a changed dependency.
        self.asset_hashes: Dict[str, str] = {
            name: (
                _graph_hash(name, content_hashes, static_dir)
                if name.endswith(".js")
                else digest
            )
            for name, digest in content_hashes.items()
        }
        self.git_sha: str = _git_short_sha(repo_root)
        self.built_at: str = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )

    def stamp_html(self, html: str) -> str:
        """Replace the ``?v=__NAME__`` placeholders in index.html with the
        content hash of each directly-referenced asset."""
        for name in HTML_STAMPED_ASSETS:
            digest = self.asset_hashes.get(name)
            if digest:
                html = html.replace(_placeholder(name), digest)
        return html

    def rewrite_js_imports(self, body: str, base: str = "") -> str:
        """Stamp ``?v=<hash>`` onto every relative ``import`` in a JS body.

        ``base`` is the importer's directory relative to the static root
        (``""`` for a root-level module) so ``./x.js`` and ``../icons/x.js``
        resolve to the right :attr:`asset_hashes` key. Imports with no
        matching entry are left untouched — robust against a dynamic import
        or a file added but not yet hashed. Any existing ``?v=…`` is
        replaced so re-rewriting an already-stamped body is idempotent.
        """
        if not self.asset_hashes:
            return body

        def _sub(match: re.Match) -> str:
            prefix, relpath, _existing, quote_close = match.group(1, 2, 3, 4)
            key = posixpath.normpath(posixpath.join(base, relpath))
            digest = self.asset_hashes.get(key)
            if not digest:
                return match.group(0)
            return f"{prefix}{relpath}?v={digest}{quote_close}"

        return _JS_IMPORT_RE.sub(_sub, body)

    def fingerprint(self) -> str:
        """Digest of everything that makes this build's served pages differ.

        Mixed into the entry document's ETag so a 304 can never outlive a
        build: the stamped HTML only names the directly-referenced assets,
        so a changed transitive ``.js`` module or a new commit must still
        change the validator.
        """
        digest = hashlib.sha256(self.git_sha.encode("utf-8"))
        for name, asset in sorted(self.asset_hashes.items()):
            digest.update(f"\0{name}={asset}".encode("utf-8"))
        return digest.hexdigest()

    def as_dict(self) -> Dict[str, str]:
        """Payload for the ``/api/version`` endpoint."""
        return {
            "git_sha": self.git_sha,
            "built_at": self.built_at,
            "asset_hash": self.asset_hashes.get("app.js", "missing"),
        }
