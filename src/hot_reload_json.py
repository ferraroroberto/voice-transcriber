"""Shared mtime-cached JSON loader for the hot-reloadable, gitignored config
files (``config/snippets.json``, ``config/vocabulary.json``,
``config/speaker_blocklist.json``).

Each of ``src/snippets.py``, ``src/vocabulary.py``, and
``src/speaker_label.py`` loads a small user-editable JSON file and
hot-reloads it on mtime change so edits take effect without a restart. This
module factors the stat → compare-to-cache → lock → parse → warn-and-fallback
dance into one place so a correctness fix lands once instead of three times.

Callers own the file's shape: they pass a ``parse_fn`` that turns the decoded
JSON value into whatever cached shape they want (a dict, a compiled regex, a
tuple of both, …) and an ``empty_value`` — the value returned (and never
cached) when the file is missing, unreadable, or ``parse_fn`` decides the
content is invalid/empty. ``parse_fn`` should signal "invalid" by returning
``empty_value`` itself (logging its own warning first) rather than raising —
only ``json.loads``/file-read failures are caught here.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable, Generic, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MtimeCachedJson(Generic[T]):
    """Hot-reloading JSON loader cached against file mtime."""

    def __init__(
        self,
        path: Path,
        parse_fn: Callable[[object], T],
        empty_value: T,
        *,
        label: Optional[str] = None,
    ) -> None:
        self.path = path
        self._parse_fn = parse_fn
        self._empty_value = empty_value
        self._label = label or path.name
        self._lock = threading.Lock()
        self._cached_mtime = 0.0
        self._cached_value: T = empty_value

    def reset(self, path: Optional[Path] = None) -> None:
        """Clear the cache (and optionally repoint ``path``) — test-only."""
        if path is not None:
            self.path = path
        self._cached_mtime = 0.0
        self._cached_value = self._empty_value

    def load(self) -> T:
        """Return the cached value, reloading from disk if ``path`` changed."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return self._empty_value
        if mtime == self._cached_mtime and self._cached_value != self._empty_value:
            return self._cached_value
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning(f"⚠️  {self._label} invalid: {exc}")
                return self._empty_value
            value = self._parse_fn(raw)
            if value == self._empty_value:
                return self._empty_value
            self._cached_mtime = mtime
            self._cached_value = value
            return value
