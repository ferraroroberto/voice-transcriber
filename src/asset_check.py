"""Whether a downloaded asset is whole enough to open.

A download that was cut off, or answered with nothing, still leaves a file
behind. Both fetch scripts ask here before they extract or accept it.
"""

from __future__ import annotations

# Standard library imports
import gzip
import zipfile
import zlib
from pathlib import Path
from typing import Optional

_CHUNK_BYTES = 1024 * 1024


def download_problem(path: Path) -> Optional[str]:
    """What is wrong with the downloaded ``path``, or ``None`` when it reads whole.

    Zip and gzip archives are read through so their checksums are verified;
    any other file is only checked for being there and non-empty.
    """
    if not path.is_file():
        return "is missing"
    if path.stat().st_size == 0:
        return "is empty (0 bytes)"
    try:
        if path.name.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
            if bad is not None:
                return f"is corrupt (bad file inside: {bad})"
        elif path.name.endswith((".tar.gz", ".tgz")):
            with gzip.open(path, "rb") as fh:
                while fh.read(_CHUNK_BYTES):
                    pass
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as exc:
        return f"is corrupt ({exc})"
    return None
