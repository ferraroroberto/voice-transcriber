"""Writers for runtime files whose path the runtime-write ratchet cannot resolve.

The archive, the databases, the whisper-server log and the operator-run scripts
write where a parameter, a config value or a command-line flag says. The ratchet
cannot follow such a path statically, so each write would need its own exempt
marker. These writers take the path, refuse it at runtime when it resolves under
one of the code folders the ratchet guards, and then write: the guarantee a
marker only claimed is enforced here. Gitignored live state that sits inside the
checkout (``archive/``, ``config/``, ``webapp/``) is outside those folders and
stays allowed.

Stdlib-only and free of any other repo import, so a script can load it first.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import IO, Any, Union

PathLike = Union[str, "os.PathLike[str]"]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The folders `[runtime_write_ratchet] code_folders` guards (its default set).
CODE_FOLDERS = ("src", "app", "scripts", "hooks", "skills")


def checked(path: PathLike) -> Path:
    """``path`` resolved, or ``ValueError`` when it sits under a code folder."""
    resolved = Path(path).expanduser().resolve()
    for name in CODE_FOLDERS:
        folder = REPO_ROOT / name
        if resolved == folder or folder in resolved.parents:
            raise ValueError(
                f"Refusing to write under the code folder {folder}: {resolved}. "
                "Runtime output belongs in archive/, config/, webapp/ or the runtime data root."
            )
    return resolved


def make_dirs(path: PathLike) -> Path:
    """Create the folder ``path`` and its parents; refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named folder; checked() above raises when it is under a code folder
    target.mkdir(parents=True, exist_ok=True)
    return target


def open_for_write(path: PathLike, mode: str = "w", **kwargs: Any) -> IO[Any]:
    """Open ``path`` with ``mode`` (creating no folders); refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named file; checked() above raises when it is under a code folder
    return target.open(mode, **kwargs)


def save_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` in one call; refuses a path under a code folder."""
    with open_for_write(path, "w", encoding=encoding) as handle:
        handle.write(text)


def save_bytes(path: PathLike, data: bytes) -> None:
    """Write ``data`` to ``path`` in one call; refuses a path under a code folder."""
    with open_for_write(path, "wb") as handle:
        handle.write(data)


def copy_file(src: PathLike, dst: PathLike) -> Path:
    """Copy the file ``src`` to ``dst``; refuses a destination under a code folder."""
    target = checked(dst)
    # runtime-write-exempt: caller-named destination; checked() above raises when it is under a code folder
    shutil.copyfile(src, target)
    return target


def move(src: PathLike, dst: PathLike) -> Path:
    """Move ``src`` to ``dst``; refuses a destination under a code folder."""
    target = checked(dst)
    # runtime-write-exempt: caller-named destination; checked() above raises when it is under a code folder
    shutil.move(str(src), str(target))
    return target


def replace(src: PathLike, dst: PathLike) -> Path:
    """Atomically swap ``src`` over ``dst``; refuses a destination under a code folder."""
    target = checked(dst)
    os.replace(src, target)
    return target


def connect_sqlite(path: PathLike, **kwargs: Any) -> sqlite3.Connection:
    """Open the SQLite file ``path``; refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named database; checked() above raises when it is under a code folder
    return sqlite3.connect(target, **kwargs)
