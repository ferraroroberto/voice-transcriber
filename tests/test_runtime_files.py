"""The guarded writers refuse a code-folder path and write anywhere else."""

from __future__ import annotations

import sqlite3

import pytest

from src import runtime_files


@pytest.mark.parametrize("folder", runtime_files.CODE_FOLDERS)
def test_checked_refuses_every_code_folder(folder):
    target = runtime_files.REPO_ROOT / folder / "nested" / "out.txt"
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.checked(target)


def test_writers_refuse_a_code_folder_without_touching_disk():
    target = runtime_files.REPO_ROOT / "src" / "_refused_runtime_file.txt"
    with pytest.raises(ValueError):
        runtime_files.save_text(target, "x")
    with pytest.raises(ValueError):
        runtime_files.make_dirs(runtime_files.REPO_ROOT / "app" / "_refused_dir")
    with pytest.raises(ValueError):
        runtime_files.connect_sqlite(runtime_files.REPO_ROOT / "scripts" / "_refused.sqlite3")
    assert not target.exists()
    assert not (runtime_files.REPO_ROOT / "app" / "_refused_dir").exists()


def test_writers_write_outside_the_code_folders(tmp_path):
    folder = runtime_files.make_dirs(tmp_path / "a" / "b")
    runtime_files.save_text(folder / "t.txt", "hello")
    runtime_files.save_bytes(folder / "b.bin", b"\x00\x01")
    runtime_files.copy_file(folder / "t.txt", folder / "copy.txt")
    runtime_files.replace(folder / "copy.txt", folder / "moved.txt")
    runtime_files.move(folder / "moved.txt", folder / "final.txt")
    with runtime_files.open_for_write(folder / "t.txt", "ab") as handle:
        handle.write(b"!")
    assert (folder / "t.txt").read_text(encoding="utf-8") == "hello!"
    assert (folder / "b.bin").read_bytes() == b"\x00\x01"
    assert (folder / "final.txt").read_text(encoding="utf-8") == "hello"

    conn = runtime_files.connect_sqlite(folder / "db.sqlite3")
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()
    assert (folder / "db.sqlite3").exists()


def test_gitignored_live_state_inside_the_checkout_stays_allowed():
    for name in ("archive", "config", "webapp"):
        assert runtime_files.checked(runtime_files.REPO_ROOT / name / "x") == (
            runtime_files.REPO_ROOT / name / "x"
        ).resolve()
